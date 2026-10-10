"""FrappeForge on the live site.

FrappeForge sandboxes are temporary and untrusted; this site is the asset. Everything that
decides what the FrappeForge agent may do here is enforced in this package:

- ``guard``: an ``auth_hooks`` entry that limits an agent user to reads and proposals;
- ``connection``: agent users, their keys, expiry and revocation;
- ``release``: reads a proposed Frappe Package release from its own tarball;
- ``deploy``: applies an approved release as the approver, records every change, rolls back.
"""
