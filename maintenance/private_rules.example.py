# Copy to private_rules.py (gitignored) and fill in the real inventory.
# build-package.py loads these and refuses to build a public package without them.
PUBLIC_RULES = (
    (r"(?i:\b<host-name>\b)", "a Windows host"),
    (r"<lan-address-regex>", "a LAN address"),
    (r"\b<account-id>\b", "<id>"),
)

PUBLIC_FORBIDDEN = (
    r"<host-name>", r"<lan-address>", r"<account-id>",
)

SECRET_LABELS = ("mattermost token", "allowed user id")

# Words that must never appear in a public tree: this host's own working records (session names,
# report filenames, wherever the write-ups are kept). tests/test_wording.py reads them from here
# when this file is present, or from TINYCMDR_LEAK_PATTERNS.
PRIVATE_WORDS = ()
