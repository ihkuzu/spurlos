# spurlos

*German for "without a trace".*

Every company that has ever sent you an email has your address, and usually much more.
spurlos finds them, asks them what they store about you and gets it deleted under the GDPR.
It runs on your own machine: no account, no server, no third party that you have to hand
your data to so that it can be deleted.

> Status: early. Reading your mailbox, finding the organisations behind it and their privacy
> contacts, deciding what to do and sending GDPR requests with deadline tracking works.
> Reading the replies with an agent and escalating follow, see [Roadmap](#roadmap).

## What it does today

```
$ spurlos demo
wrote 215 demo messages to data/demo.mbox, 215 imported into ~/.spurlos/vault.db

$ spurlos census
12 organisations have your email address (from 215 messages, 10 from private mailboxes left out).
7 of them have not written for over two years: likely forgotten accounts.

organisation                 domain                         mails  first       last        newsletter
CodeCamp Online              codecamp.example                  28  2017-11-12  2020-10-13  75%
Kartenhaus Games             kartenhaus-games.example          27  2014-10-25  2016-09-29  0%
Apotheke Rosengarten         apotheke-rosengarten.example      24  2024-11-05  2026-09-27  0%
...

$ spurlos triage
12 organisations, 5 with a known privacy contact.
priority: 7 high, 5 medium, 0 low

[high  ] Kartenhaus Games (kartenhaus-games.example) - entertainment
         erasure: forgotten account: account, password mails, none for 2+ years
         contact: privacy@kartenhaus-games.example
[high  ] Apotheke Rosengarten (apotheke-rosengarten.example) - health
         access: may hold special category data (Art. 9); see what they keep first
         contact: datenschutz@apotheke-rosengarten.example
...
```

The demo mailbox is made up and uses `.example` domains, so nothing in it can reach a real
company. Run it on your own mail to see your real numbers.

## How it decides

For every organisation spurlos looks at three things:

1. **Who they are.** The sender domain is looked up in the open
   [Datenanfragen.de](https://www.datarequests.org) database: about 3,000 organisations with
   their privacy contact, category and preferred way of contact, released under CC0. A copy
   ships with spurlos, so lookups work offline. `mail.news.zalando.de` finds Zalando SE and
   `datenschutz@zalando.de`.
2. **What they likely hold.** Subject lines show whether there is more than an email address:
   welcome and verification mails (an account), orders and invoices (name, address, payment),
   password resets, contracts and bookings. Patterns cover German, English and Turkish.
3. **What kind of organisation it is.** Taken from the database first, then from simple name
   rules (Apotheke, Bank, Versicherung, ...). Only what is still unknown goes to a local model
   with `--llm`, and its answer has to be one of a fixed list of categories.

From that it suggests one action:

| situation | action | priority |
|---|---|---|
| address dealers, ad networks, credit agencies | access + objection (Art. 15, 21) | high |
| health, religion, politics (Art. 9 data) | access first | high |
| account with no mail for two years | erasure (Art. 17) | high |
| no mail for two years | erasure | medium |
| active account, contract, bank, insurer | access | medium |
| newsletter only | unsubscribe link | low |

```
spurlos triage                      # rules only, no model
spurlos triage --llm                # ask a local Ollama model for unknown categories
spurlos triage --llm --model qwen2.5:7b
spurlos registry show dhl.de        # all matching records
spurlos contact shop.de --email datenschutz@shop.de   # add a contact the database lacks
spurlos registry update             # fetch the latest database from GitHub
```

The model runs through [Ollama](https://ollama.com) on your machine. A 3B model is enough for
this; no subject line leaves your computer.

## Sending requests

```
spurlos profile --name "Max Mustermann" --email max@gmail.com
spurlos request new zalando.de --kind access          # one draft
spurlos request batch --priority high                 # drafts for every high item in triage
spurlos request send SP-7K2Q9M                        # shows the mail, asks, then sends
spurlos request list
```

```
SP-XQMLU3  erasure   kartenhaus-games.example       email   draft
SP-KN3KEZ  erasure   codecamp.example               email   sent 2026-09-01, OVERDUE by 21 days
SP-F874UW  access    apotheke-rosengarten.example   email   draft
```

- **The letters are fixed legal text, not generated.** They are the access (Art. 15), erasure
  (Art. 17) and objection (Art. 21) templates of Datenanfragen.de, in German or English
  depending on the company. No model writes or changes them.
- **Only the data needed to find you.** Name, email and a reference number. Your postal
  address is added only for companies whose record says they need it.
- **Nothing is sent without you.** `send` prints the full mail and waits for you to type
  `yes`. It goes out from your own address over your provider's SMTP, with a copy to yourself
  as proof of the date. The password is asked for each time and not stored.
- **Or send it yourself.** `request export ID --out request.eml` writes a draft your mail
  program opens; afterwards `request mark-sent ID` starts the clock.
- **Deadlines.** Companies have one month to answer (Art. 12(3)); `request list` counts the
  days and marks overdue requests. Each request carries a reference like `SP-7K2Q9M` in the
  subject, so replies can be matched to it later.
- **Web forms and letters** are never emailed. spurlos shows where to send the text instead.
- **Example addresses are blocked.** Mail to `.example`, `.test` and similar domains never
  leaves the machine, so the demo cannot reach anyone.

## Privacy by design

- **Headers only.** Over IMAP, spurlos asks the server for five header fields
  (`From`, `Date`, `Subject`, `Message-ID`, `List-Unsubscribe`) with `BODY.PEEK`, so no
  message body is downloaded and nothing is marked as read. The mailbox is opened read-only.
  A test checks exactly this.
- **Local only.** Everything goes into one SQLite file, `~/.spurlos/vault.db`
  (or `$SPURLOS_HOME`), readable only by you. Nothing is uploaded anywhere.
- **No data out.** The only network calls are to your own mail server (reading headers and,
  when you confirm, sending your requests), to a model on your own machine and, if you ask
  for it, to GitHub to download the public contact database.
- **Your own data only.** spurlos works on your own mailbox. It does not search for other
  people and will not send requests in anyone else's name.

## Install

```
pip install git+https://github.com/ihkuzu/spurlos.git
```

Python 3.10 or newer. Works on Linux, macOS and Windows.

## Use it on your mail

**From a file.** Export your mail as mbox (Google Takeout, Thunderbird with the
ImportExportTools add-on) or as a folder of `.eml` files:

```
spurlos import mbox ~/Downloads/All-mail.mbox
spurlos import eml ~/mail-export/
```

**Over IMAP.** Most providers need an app password for this (Gmail: Google account →
Security → App passwords). The password is read from `SPURLOS_IMAP_PASSWORD` or asked for
and is never stored.

```
spurlos import imap --host imap.gmail.com --user you@gmail.com --list-folders
spurlos import imap --host imap.gmail.com --user you@gmail.com --folder "[Gmail]/All Mail"
spurlos import imap --host imap.gmx.net --user you@gmx.de --all-folders --since 2015-01-01
```

Then:

```
spurlos census              # top 25
spurlos census --limit 0    # all
spurlos census --json       # for other tools
spurlos me other@address.de # leave out more of your own addresses
```

Mails from private mailboxes (gmail.com, web.de, gmx.de, ...) are counted as people and
left out of the list.

## Roadmap

1. Read the mailbox and list the organisations (done)
2. Privacy contacts from the open database, signals for real accounts and categories from
   rules or a local model, plus a suggested action per organisation (done)
3. GDPR requests from fixed legal templates in German and English, sent from your own address
   only after you confirm, with the one-month deadline tracked (done)
4. A reply agent that matches answers to requests and tells you what happened: confirmed,
   identity check, refused, data attached or no answer
5. Reminders after the one-month deadline and a complaint draft for the right data
   protection authority
6. Reading the data exports companies send back, so you can see who knows what about you
7. Docs, demo and a first release

## Not legal advice

spurlos helps you use rights the GDPR gives you. It is not a lawyer, and the letters it
prepares are templates you check before you send them.

## Credits

Privacy contacts, supervisory authorities and request templates come from
[datenanfragen/data](https://github.com/datenanfragen/data) (CC0 1.0). Missing or wrong
records are best fixed there, so every tool that uses the database gets the fix.

## License

MIT
