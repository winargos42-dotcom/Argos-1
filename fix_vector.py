content = open('src/knowledge/vector_store.py', 'r', encoding='utf-8').read()
old = '                class _Embedder:\n    name = "argos-embedder"\n'
new = '                class _Embedder:\n                    name = "argos-embedder"\n'
content = content.replace(old, new)
open('src/knowledge/vector_store.py', 'w', encoding='utf-8').write(content)
print('OK')
