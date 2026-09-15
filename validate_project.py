#!/usr/bin/env python3
"""
Скрипт для финальной проверки проекта перед релизом.
Проверяет синтаксис, импорты, кодировку и другие потенциальные проблемы.
"""

import os
import sys
import ast
import importlib.util
from pathlib import Path
import subprocess

from scripts.check_python_syntax import check_source, print_selection, tracked_python_sources


class ProjectValidator:
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir).resolve()
        self.errors = []
        self.warnings = []
        self.success = []
    
    def print_header(self, text):
        """Печатает заголовок секции."""
        print(f"\n{'='*70}")
        print(f"  {text}")
        print(f"{'='*70}\n")
    
    def _python_files(self):
        """Cache the shared selection and preserve discovery failures."""
        if not hasattr(self, "_selected_python_files"):
            try:
                files, excluded = tracked_python_sources(self.project_dir)
                print_selection(files, excluded)
                if not files:
                    self.errors.append("No tracked first-party Python files")
                self._selected_python_files = files
            except RuntimeError as exc:
                self.errors.append(str(exc))
                self._selected_python_files = []
        return self._selected_python_files

    def check_python_syntax(self):
        """Validate tracked first-party source without executing it."""
        self.print_header("🔍 ПРОВЕРКА СИНТАКСИСА PYTHON")
        for path in self._python_files():
            try:
                check_source(path, self.project_dir)
                self.success.append(f"✅ {path.relative_to(self.project_dir)}")
            except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
                message = f"❌ {path.relative_to(self.project_dir)}: {exc}"
                self.errors.append(message)
                print(message)
        if not self.errors:
            print("✅ Все проверяемые файлы прошли проверку синтаксиса")

    def check_imports(self):
        """Check top-level import availability without importing application code."""
        self.print_header("📦 ДОСТУПНОСТЬ ИМПОРТОВ (БЕЗ ЗАПУСКА)")
        unavailable = []
        availability = {}
        for path in self._python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, SyntaxError):
                continue
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names.add(node.module.split(".")[0])
            for name in sorted(names):
                if name not in availability:
                    local = ((self.project_dir / f"{name}.py").is_file()
                             or (self.project_dir / name).is_dir())
                    try:
                        availability[name] = local or importlib.util.find_spec(name) is not None
                    except (ImportError, AttributeError, ValueError):
                        availability[name] = False
                if not availability[name]:
                    unavailable.append(f"⚠️ {path.relative_to(self.project_dir)}: {name}")
        self.warnings.extend(unavailable)
        for message in unavailable[:10]:
            print(message)
        if len(unavailable) > 10:
            print(f"... и ещё {len(unavailable) - 10} недоступных импортов")
        print("Проверено наличие модулей; работоспособность импортов не подтверждается.")

    def check_requirements(self):
        """Проверяет наличие requirements.txt и его корректность."""
        self.print_header("📋 ПРОВЕРКА ЗАВИСИМОСТЕЙ")
        
        req_files = [
            self.project_dir / 'requirements.txt',
            self.project_dir / 'requirements' / 'base.txt',
            self.project_dir / 'requirements' / 'production.txt'
        ]
        
        found = False
        for req_file in req_files:
            if req_file.exists():
                found = True
                print(f"✅ Найден: {req_file.relative_to(self.project_dir)}")
                
                try:
                    with open(req_file, 'r') as f:
                        lines = f.readlines()
                    print(f"   Пакетов: {len([l for l in lines if l.strip() and not l.startswith('#')])}")
                except Exception as e:
                    print(f"   ⚠️  Ошибка чтения: {e}")
        
        if not found:
            msg = "⚠️  requirements.txt не найден"
            self.warnings.append(msg)
            print(msg)
    
    def check_structure(self):
        """Проверяет структуру проекта."""
        self.print_header("📁 ПРОВЕРКА СТРУКТУРЫ ПРОЕКТА")
        
        important_files = [
            'README.md',
            'LICENSE',
            '.gitignore',
            'setup.py',
            'pyproject.toml'
        ]
        
        for file_name in important_files:
            file_path = self.project_dir / file_name
            if file_path.exists():
                print(f"✅ {file_name}")
            else:
                print(f"⚠️  {file_name} (необязательно)")
        
        # Проверка основных директорий
        important_dirs = ['src', 'tests', 'docs']
        
        print("\nДиректории:")
        for dir_name in important_dirs:
            dir_path = self.project_dir / dir_name
            if dir_path.exists():
                py_files = len(list(dir_path.rglob('*.py')))
                print(f"✅ {dir_name}/ ({py_files} Python файлов)")
            else:
                print(f"⚠️  {dir_name}/ (не найдена)")
    
    def check_git_status(self):
        """Проверяет статус Git репозитория."""
        self.print_header("🌿 ПРОВЕРКА GIT СТАТУСА")
        
        if not (self.project_dir / '.git').exists():
            print("⚠️  Git репозиторий не инициализирован")
            return
        
        try:
            # Проверка неотслеживаемых файлов
            result = subprocess.run(
                ['git', 'status', '--porcelain'],
                cwd=self.project_dir,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                changes = result.stdout.strip()
                if changes:
                    print("⚠️  Есть незакоммиченные изменения:")
                    for line in changes.split('\n')[:5]:
                        print(f"   {line}")
                    remaining = len(changes.split('\n')) - 5
                    if remaining > 0:
                        print(f"   ... и еще {remaining} файлов")
                else:
                    print("✅ Нет незакоммиченных изменений")
                
                # Проверка текущей ветки
                result = subprocess.run(
                    ['git', 'branch', '--show-current'],
                    cwd=self.project_dir,
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    branch = result.stdout.strip()
                    print(f"📍 Текущая ветка: {branch}")
            
        except Exception as e:
            print(f"⚠️  Не удалось проверить Git статус: {e}")
    
    def generate_report(self):
        """Генерирует финальный отчет."""
        self.print_header("📊 ФИНАЛЬНЫЙ ОТЧЕТ")
        
        print(f"✅ Успешно: {len(self.success)} файлов")
        print(f"⚠️  Предупреждения: {len(self.warnings)}")
        print(f"❌ Ошибки: {len(self.errors)}")
        
        if self.errors:
            print("\n❌ КРИТИЧЕСКИЕ ОШИБКИ:")
            for error in self.errors:
                print(f"\n{error}")
            return False
        else:
            print("\n✅ СТАТИЧЕСКИЕ ПРОВЕРКИ ПРОЙДЕНЫ")
            return True
    
    def run_all_checks(self):
        """Запускает все проверки."""
        print("🚀 ФИНАЛЬНАЯ ПРОВЕРКА ПРОЕКТА")
        
        self.check_python_syntax()
        self.check_imports()
        self.check_requirements()
        self.check_structure()
        self.check_git_status()
        
        return self.generate_report()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        project_dir = sys.argv[1]
    else:
        current = Path.cwd()
        if (current / 'src').exists():
            project_dir = str(current)
        elif current.name == 'src':
            project_dir = str(current.parent)
        else:
            project_dir = str(current)
    
    validator = ProjectValidator(project_dir)
    success = validator.run_all_checks()
    
    sys.exit(0 if success else 1)
