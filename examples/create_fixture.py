"""Create a wholly invented AcroForm and matching plan in a new directory."""
import argparse
import json
from pathlib import Path
import fitz

parser = argparse.ArgumentParser()
parser.add_argument('directory')
args = parser.parse_args()
root = Path(args.directory)
root.mkdir(parents=True, exist_ok=False)
doc = fitz.open()
page = doc.new_page()
widget = fitz.Widget()
widget.field_name = 'example_name'
widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
widget.rect = fitz.Rect(40, 40, 260, 70)
widget.field_value = 'Example'
page.add_widget(widget)
doc.save(root / 'example.filled.pdf')
doc.close()
(root / 'reference.txt').write_text('Invented source for a test.\n', encoding='utf-8')
(root / 'fields.json').write_text(json.dumps([{'field': 'example_name', 'required': True}], indent=2), encoding='utf-8')
(root / 'fill_plan.json').write_text(json.dumps([{'field': 'example_name', 'value': 'Example', 'source': 'reference.txt'}], indent=2), encoding='utf-8')
print(root)
