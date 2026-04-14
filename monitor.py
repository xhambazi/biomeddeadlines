import json
import urllib.request
import hashlib
from html.parser import HTMLParser

class SimpleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
    def handle_data(self, data):
        self.text.append(data)

def get_page_hash(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8')
            parser = SimpleParser()
            parser.feed(html)
            text = "".join(parser.text).replace(" ", "").replace("\n", "")
            return hashlib.md5(text.encode('utf-8')).hexdigest()
    except Exception:
        return None

with open('conferences.json', 'r') as f:
    data = json.load(f)

changed = False
for conf in data:
    current_hash = get_page_hash(conf['url'])
    if current_hash:
        if conf.get('pageHash') != current_hash:
            print(f"🚨 UPDATE DETECTED: {conf['name']} website has changed! Review: {conf['url']}")
            conf['pageHash'] = current_hash
            changed = True

if changed:
    with open('conferences.json', 'w') as f:
        json.dump(data, f, indent=2)
