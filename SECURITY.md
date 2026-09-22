# Security policy

Security fixes target the latest release. Older versions have no separate
maintenance guarantee. JevLab is maintained on a best-effort basis; no response
or resolution deadline is promised.

## Report a vulnerability

Do not post exploit details, credentials, or private data in a public issue or
pull request. Use [Report a vulnerability](https://github.com/javsanesq/jevlab/security/advisories/new),
GitHub's private reporting option, which is enabled for this repository.

That option depends on the repository setting; adding this document does not
enable it. If it is unavailable, open an issue asking the maintainer for a private
security contact, without describing the vulnerability. Wait for a private route
before sending the details. Never include a real API key, even in a private report.

A useful private report includes the affected version, a minimal reproduction
using synthetic data, the impact, and any suggested mitigation. Test only with
data and accounts you control. If a key was exposed, revoke or rotate it through
its provider rather than waiting for a code fix.

## Scope and data handling

Report credential leaks, unsafe file access, or other vulnerabilities in JevLab
or its generated exports here. Issues in TypeSafe or a coach provider's service
belong with that provider. Ordinary bugs can use the public bug-report form.

Run inputs and responses are stored in the local profile; the application does
not encrypt that database. Live calls send selected inputs to their provider.
Host application logging and tracing can also retain inputs from exported code.
See [storage and privacy](docs/REFERENCE.md#storage-costs-and-privacy) before using
private data or attaching diagnostic output to a report.
