import os

# 清除浏览器缓存的方法：修改静态文件的内容使其变化
static_dir = os.path.join(os.path.dirname(__file__), 'lab_device_manager', 'web', 'static')

# 修改 app.js 添加注释（不会影响功能）
app_js_path = os.path.join(static_dir, 'app.js')
with open(app_js_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 在文件末尾添加时间戳注释
import time
timestamp = int(time.time())
if '// cache_bust' not in content:
    content = content.rstrip() + f'\n\n// cache_bust={timestamp}\n'
    with open(app_js_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("app.js cache busted")
else:
    content = content.replace(r'// cache_bust=\d+', f'// cache_bust={timestamp}')
    with open(app_js_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("app.js cache updated")

# 修改 index.html 添加时间戳
index_html_path = os.path.join(static_dir, 'index.html')
with open(index_html_path, 'r', encoding='utf-8') as f:
    content = f.read()

if 'cache_bust' not in content:
    content = content.replace('</head>', f'<!-- cache_bust={timestamp} --></head>')
    with open(index_html_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("index.html cache busted")
else:
    content = content.replace(r'<!-- cache_bust=\d+ -->', f'<!-- cache_bust={timestamp} -->')
    with open(index_html_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("index.html cache updated")