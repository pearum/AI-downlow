"""Account / series / folder / playlist collection downloader.

Generic, platform-neutral collection handling:
- ``base.CollectionProvider`` — the contract every platform implements
- ``providers``       — concrete YouTube / TikTok / Facebook providers
- ``store``           — SQLite persistence for resume + status tracking
- ``engine``          — orchestration (scan, paginate, queue, resume,
  retry-failed, completion reports)
"""