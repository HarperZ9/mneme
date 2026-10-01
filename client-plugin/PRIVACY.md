# Privacy

Mneme runs on the user's computer. The publisher operates no backend for this package.
The connected client and its model can see tool arguments and results under that client's terms.
No model is included. Any model endpoint configured by the user belongs to that user.
Local access is granted to the running process by the operating system; a client permission dialog is not an OS sandbox.
Do not connect private stores or directories to an untrusted client. Stop the client process to disconnect.
Snapshot writes and cleanup use a state-specific sibling directory. Legacy global copies remain outside this client's authority and are reported as unchecked by forget receipts.

## What it reads, stores and sends

Mneme reads and, with the memory-write grant, writes the SQLite file you select at
install. Replay snapshots go in a folder beside that file. Mneme opens no network
connection and collects no usage statistics.

## Retention and support

Memories stay in your SQLite file until you remove them with `mneme.forget` or delete
the file. Support and security reports: https://github.com/HarperZ9/mneme/issues
