# Changelog

## 0.1.0 (2026-10-27)

First release.

- Read mail headers from mbox, `.eml` folders or IMAP (read-only, headers only) and list the
  organisations that have your address, with forgotten accounts marked.
- Privacy contacts from the Datenanfragen.de database, signals for real accounts and a
  suggested action per organisation, from rules or a local model.
- Access, erasure and objection requests from fixed legal templates in German and English,
  sent by SMTP only after you type `yes`, or exported as `.eml`. The one-month deadline is
  tracked.
- A LangGraph reply agent that matches answers to requests, labels them and sets the next
  step, measured on 40 example replies.
- Reminders, complaints to the data protection authority of your federal state and a to-do
  list.
- A local web page on 127.0.0.1 with a token and a strict Content-Security-Policy.
- Reading the data exports companies send back into a map of who holds what, keeping only
  field names and masked examples.
- A read-only MCP server for AI assistants.
- A Docker image.
