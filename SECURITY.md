# Security Policy

## Supported versions

Security fixes are provided for the latest released minor version.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub private
vulnerability reporting for this repository. Include:

- the affected version and operating system;
- a minimal synthetic deck or directory layout;
- the observed and expected behavior;
- whether the problem can escape an include root, execute code, overwrite a
  file, exhaust resources, or expose local data.

Reports are reviewed as maintainer availability permits. Acknowledgement,
validation, and coordinated disclosure timing depend on impact and reproducibility.

## Security model

SPICE decks are untrusted text. SpiceTrellis must not invoke a simulator,
shell, network service, plugin, or dynamic Python expression while reading a
deck. Include paths are constrained after canonical resolution. Output files
are written only when the caller explicitly supplies a destination.

SpiceTrellis does not provide process-level resource isolation. Applications
accepting public uploads should enforce file-count, byte, CPU, and memory limits
around the process.
