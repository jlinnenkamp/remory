---
description: Consolidate pending raw entries for the current topic into state.md.
---
<!-- remory: template_version=1 -->
Sleep is a deliberate, separate step in Remory — it runs outside the chat session, not inside it. To consolidate this topic's pending entries:

1. Exit this chat session (Ctrl+D, or `/exit` if your terminal supports it).
2. Run `remory sleep <topic>` where `<topic>` is the name of this directory (the basename of `pwd`).

You'll see a summary when it finishes, and `_review.md` will be updated if the topic's schema runs critique.
