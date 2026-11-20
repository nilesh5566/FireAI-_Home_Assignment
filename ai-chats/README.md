Fix the crash when data.csv is open in Excel, and add a test for it.”
Short prompts made me stop and ask you questions three times in this session. Each time cost you a round of back-and-forth.It’s easier to check, easier to commit separately, and it gives you the step-by-step history the rubric wants instead of one big commit.
Keep it to standard-library Python 3.9.

3. Ask for one thing at a time

One bug, one feature or one file per message.
It’s easier to check, easier to commit separately, and it gives you the step-by-step history the rubric wants instead of one big commit.

4. Say what “done” looks like

“Done means python reorder.py check exits 0 and the tests pass.”
“Keep it to standard-library Python 3.9.”

5. Make it show you, not just tell you

“Run it and show me the output.”
“What did you change, and why?”
Then actually read the diff. On the call they’ll ask you about the code, not the AI.

6. Keep your own work yours

Use AI for: code, debugging, tests, explaining errors, checking your writing against the brief.
Don’t use it for: fieldwork data, quotes from the owner, photos, or the reflection. The rubric checks for exactly these, and the call questions (“pick a row, where were you?”) will expose them.
Never ask it to “generate sample data” and then put that in data.csv.

7. Ask it to criticise, not just to build

“What would a strict grader flag in this project?”
“What will this tool get wrong in a real shop?” That gives you material for the README’s “what it gets wrong” section.
“Ask me 5 questions the interviewer might ask about this code.”

8. Tell it about mistakes plainly

✅ “That’s wrong: bangle sizes 2.4 and 2.6 must stay separate, because the owner reorders by size.”
That kind of correction is also good material for your reflection: where the AI got it wrong and you caught it.

9. Keep a 1-line note after each chat

“AI helped: found the Excel-lock crash. Wasted time: it asked me 3 clarifying questions because I typed ‘correct it’.”
Your reflection.md then almost writes itself, in your own words, and it won’t read like it “could fit any project”.

10. Save every chat as you go

Export each conversation into ai-chats/ right away, including the one that wrote reorder.py. An empty folder is a red flag; a full one shows honest use.

A template you can reuse:

Context: [project in 1 line]. Goal: [one specific thing]. Constraints: [Python 3.9, no libraries, don’t touch data.csv]. Done when: [test or command passes]. Show me the diff and the output.
