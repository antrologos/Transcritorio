# Code Signing Policy

## About signed releases

Code signing for the Windows installer of Transcritorio is provided
for free by [SignPath.io](https://signpath.io/), with a certificate
issued by the [SignPath Foundation](https://signpath.org/).

Signed releases are published exclusively at:
- GitHub Releases: https://github.com/antrologos/Transcritorio/releases

Only releases distributed from this URL with a SignPath Foundation
signature should be considered authentic.

## Authorization & responsibilities

Transcritorio is maintained by Rogerio Jeronimo Barbosa
(IESP-UERJ / CERES, https://antrologos.github.io). The maintainer
is the sole person authorized to:

- Tag and publish new releases on GitHub.
- Approve code-signing requests on the SignPath dashboard.
- Add or remove team members and modify signing policies.

External contributions are reviewed by the maintainer before being
merged into the `main` branch on GitHub. Build artifacts are produced
by the public CI workflow `.github/workflows/release.yml`, which runs
on tag pushes and is auditable in the repository's Actions tab.

### Roles

- **Authors**: Rogerio Jeronimo Barbosa
- **Reviewers**: Rogerio Jeronimo Barbosa
- **Approvers**: Rogerio Jeronimo Barbosa

All team members enforce multi-factor authentication on both GitHub
and SignPath.io.

## Privacy

Transcritorio runs entirely offline on the user's machine. The app
does not collect, transmit, or share any data with the maintainer
or third parties. Audio files, transcripts, and project metadata
never leave the user's device.

The application does check Hugging Face Hub on first run to download
ASR/diarization model weights; this download is initiated only by
explicit user action in the setup wizard and uses the public
huggingface.co API. No telemetry of any kind is sent.

## Reporting issues

Security issues should be reported to rogerio.barbosa@iesp.uerj.br.
General issues: https://github.com/antrologos/Transcritorio/issues

---

Last updated: 2026-04-27
Maintainer: Rogerio Jeronimo Barbosa <rogerio.barbosa@iesp.uerj.br>
