import json
import os

filepath = 'ui/data/questions.json'
with open(filepath, 'r', encoding='utf-8') as f:
    questions = json.load(f)

# The original file has 49 questions (27 RW, 22 Math)
# We want to duplicate them so there are 98 total.
# The user's views.py filters them by type and then slices.
# If we just copy the 49 questions and append them, we will have 54 RW and 44 Math in total.

if len(questions) == 49:
    new_questions = []
    # deep copy the original questions to avoid reference issues
    import copy
    copied_questions = copy.deepcopy(questions)
    
    # Assign new IDs for the copied questions
    start_id = 50
    for q in copied_questions:
        q['id'] = start_id
        start_id += 1
        new_questions.append(q)
        
    questions.extend(new_questions)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(questions, f, indent=4, ensure_ascii=False)
    print("Duplicated questions. Total:", len(questions))
else:
    print("Questions length is not 49. Current length:", len(questions))
