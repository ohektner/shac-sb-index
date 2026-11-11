# Progress page

`index.html` is a one-page, plain-language view of where the project stands: the gates, every task's status, what the model is today, the statistics it is judged by, and how each calculation was chosen. It is written for Owen.

A scheduled cloud routine updates it each morning from `docs/STATUS.md`, `docs/PLAN.md`, `docs/MODEL.md`, `docs/DECISIONS.md` and the open pull requests, and emails it to Owen as an attachment. The routine does not commit its daily copy; to change the layout or wording for good, edit this file through a PR.

Open it in any browser. It needs no build step; fonts come from Google Fonts and fall back to system fonts offline.
