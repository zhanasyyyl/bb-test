import json
import re

with open('ui/data/questions.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

def wrap_numbers(text):
    if not text:
        return text
    # Split by $ to separate math mode and text mode
    parts = text.split('$')
    for i in range(0, len(parts), 2): # Even indices are text mode
        # Replace numbers in text mode, but avoid replacing inside words if needed.
        # \b\d+(?:\.\d+)?\b
        parts[i] = re.sub(r'\b(\d+(?:\.\d+)?)\b', r'$\1$', parts[i])
    return '$'.join(parts)

for q in data:
    if q.get('type') == 'math':
        if 'passage' in q:
            q['passage'] = wrap_numbers(q['passage'])
        if 'question' in q:
            q['question'] = wrap_numbers(q['question'])
        if 'options' in q:
            new_options = []
            for opt in q['options']:
                # If option doesn't have $, wrap the whole thing if it's just a number,
                # or wrap all numbers.
                new_options.append(wrap_numbers(opt))
            q['options'] = new_options

with open('ui/data/questions.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=4, ensure_ascii=False)
