Runtime resources packaged with With.

The GA release process must place the audited Ed25519 public key at
`release-public-key.pem`. Until that immutable trust root is present,
`eric-memory update apply` intentionally fails closed.
