# ModOS-local sepatch patch

This directory vendors `chenxiaolong/sepatch` at commit
`7634748c016165489c307a50f186aeb4faa5feb0` and the nested
`platform/external/selinux` source at commit
`085c131ad1b984bfa8ffdafee7a976e9d89f403c`.

The sole ModOS source change adds `PolicyDb::has_allow_rule`, a read-only
lookup of one `AVTAB_ALLOWED` permission bit. The helper validates the same
IDs as `set_rule`, searches the same AVTAB tuple, and does not modify policy
state. This lets the patcher verify the exact `(source, target, class,
permission)` allow bit after serialization and reparsing without treating
removal of `auditallow` or `dontaudit` entries as a failed round trip.

The copied `LICENSE` and `external/selinux/LICENSE` files remain with their
respective upstream source. The source and licenses are included in the build
selection digest.

The vendored build closure is limited to sepatch's Cargo manifest, Rust
source, build script, upstream license, the `wrapper.h` bridge, the pinned
libsepol headers and source files referenced by that build script, and
sepatch's `selinux_hackery` C wrappers. The rest of the
66 MB SELinux checkout (tests, command-line tools, Python modules, documentation,
and unrelated projects) is excluded. The closure is about 2.6 MB in this tree.
