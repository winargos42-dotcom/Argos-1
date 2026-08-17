#!/usr/bin/env python3
"""
Скрипт для финальной проверки проекта перед релизом.
Проверяет синтаксис, импорты, кодировку и другие потенциальные проблемы.
"""

import os
import sys
import ast
import py_compile
from pathlib import Path
import subprocess


class ProjectValidator:
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.errors = []
        self.warnings = []
        self.success = []
    
    def print_header(self, text):
        """Печатает заголовок секции."""
        print(f"\n{'='*70}")
        print(f"  {text}")
        print(f"{'='*70}\n")
    
    def check_python_syntax(self):
        """Проверяет синтаксис всех Python файлов."""
        self.print_header("🔍 ПРОВЕРКА СИНТАКСИСА PYTHON")
        
        python_files = list(self.project_dir.rglob('*.py'))
        python_files = [f for f in python_files if not any(
            part in f.parts for part in ['venv', '.venv', '__pycache__', 'node_modules', 'argos_deploy']
        )]
        
        print(f"Найдено Python файлов: {len(python_files)}\n")
        
        for py_file in python_files:
            try:
                # Проверка UTF-8 кодировки
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Проверка синтаксиса через AST
                ast.parse(content, filename=str(py_file))
                
                # Компиляция
                py_compile.compile(str(py_file), doraise=True)
                
                self.success.append(f"✅ {py_file.relative_to(self.project_dir)}")
                
            except UnicodeDecodeError as e:
                error_msg = f"❌ Ошибка кодировки: {py_file.relative_to(self.project_dir)}\n   {e}"
                self.errors.append(error_msg)
                print(error_msg)
                
            except SyntaxError as e:
                error_msg = f"❌ Синтаксическая ошибка: {py_file.relative_to(self.project_dir)}\n   Строка {e.lineno}: {e.msg}"
                self.errors.append(error_msg)
                print(error_msg)
                
            except Exception as e:
                error_msg = f"❌ Ошибка: {py_file.relative_to(self.project_dir)}\n   {e}"
                self.errors.append(error_msg)
                print(error_msg)
        
        if not self.errors:
            print("✅ Все файлы прошли проверку синтаксиса!")
    
    def check_imports(self):
        """Проверяет импорты в Python файлах."""
        self.print_header("📦 ПРОВЕРКА ИМПОРТОВ")
        
        python_files = list(self.project_dir.rglob('*.py'))
        python_files = [f for f in python_files if not any(
            part in f.parts for part in ['venv', '.venv', '__pycache__', 'node_modules', 'argos_deploy']
        )]
        
        import_errors = []
        
        for py_file in python_files:
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                tree = ast.parse(content)
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            try:
                                __import__(alias.name.split('.')[0])
                            except ImportError:
                                msg = f"⚠️  {py_file.relative_to(self.project_dir)}: import {alias.name}"
                                import_errors.append(msg)
                    
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            try:
                                __import__(node.module.split('.')[0])
                            except ImportError:
                                msg = f"⚠️  {py_file.relative_to(self.project_dir)}: from {node.module}"
                                import_errors.append(msg)
                
            except Exception as e:
                continue
        
        if import_errors:
            print("Обнаружены недоступные импорты (могут быть опциональными):\n")
            for error in import_errors[:10]:  # Показываем первые 10
                print(error)
            if len(import_errors) > 10:
                print(f"\n... и еще {len(import_errors) - 10} импортов")
            self.warnings.extend(import_errors)
        else:
            print("✅ Все импорты доступны!")
    
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
            print("\n🎉 ПРОЕКТ ГОТОВ К РЕЛИЗУ!")
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
