"""Explicit media metadata command; never starts capture or playback."""
from src.media_inventory import MediaInventory
from src.modules.base import BaseModule

_inventory = MediaInventory()


class MediaDevicesModule(BaseModule):
    module_id = 'media_devices'
    title = 'Медиаустройства'
    COMMANDS = {'аудио видео устройства', 'медиа устройства', 'камеры и микрофоны'}

    def can_handle(self, text, lowered):
        return lowered.strip() in self.COMMANDS

    def handle(self, text, lowered, admin=None, flasher=None):
        if not self.can_handle(text, lowered):
            return None
        snapshot = _inventory.snapshot()
        lines = ['Медиаустройства — только метаданные; запись и воспроизведение не проверялись.']
        for row in snapshot['devices']:
            lines.append(f"• {row['name']} [{row['source']}/{row['kind']}]: {row['state']}")
        for source, state in snapshot['sources'].items():
            if state['status'] != 'ok':
                lines.append(f'• {source}: состояние неизвестно — метаданные недоступны.')
            elif state['count'] == 0:
                lines.append(f'• {source}: медиаустройства не обнаружены.')
        lines.append('Несколько video-узлов могут принадлежать одной камере; ADB не подтверждает аудио/видеопоток.')
        if snapshot.get('cached'):
            lines.append('Использован кэш метаданных.' if not snapshot.get('stale') else 'Использован устаревший кэш; обновление выполняется.')
        return '\n'.join(lines)
