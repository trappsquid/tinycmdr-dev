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

SECRET_LABELS = ("web ui token", "mattermost token", "allowed user id")
