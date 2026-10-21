// SPDX-License-Identifier: AGPL-3.0-or-later

use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::unix::fs::MetadataExt;
use std::path::Path;

use sepatch::{PolicyDb, RuleAction};

fn fail(message: impl AsRef<str>) -> ! {
    eprintln!("exfatprogs-sepolicy-patcher: {}", message.as_ref());
    std::process::exit(1);
}

fn required_id<T>(value: Option<T>, name: &str) -> T {
    value.unwrap_or_else(|| fail(format!("required SELinux policy symbol is absent: {name}")))
}

fn patch(path: &Path) -> Result<bool, String> {
    let path_metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect policy file: {error}"))?;
    if !path_metadata.file_type().is_file() {
        return Err("policy path is not a regular file".into());
    }

    let mut input = File::open(path).map_err(|error| format!("cannot open policy: {error}"))?;
    let opened_metadata = input
        .metadata()
        .map_err(|error| format!("cannot inspect opened policy: {error}"))?;
    if !opened_metadata.is_file()
        || opened_metadata.dev() != path_metadata.dev()
        || opened_metadata.ino() != path_metadata.ino()
    {
        return Err("policy file changed while it was opened".into());
    }

    let mut source = Vec::new();
    input
        .read_to_end(&mut source)
        .map_err(|error| format!("cannot read policy: {error}"))?;

    let mut warnings = Vec::new();
    let mut policy = PolicyDb::from_raw(&source, &mut warnings)
        .map_err(|error| format!("cannot parse compiled policy: {error}; {warnings:?}"))?;
    for warning in warnings {
        eprintln!("exfatprogs-sepolicy-patcher: policy parser warning: {warning}");
    }

    let source_type = required_id(policy.get_type_id("fsck_untrusted"), "fsck_untrusted");
    let target_type = required_id(policy.get_type_id("sysfs"), "sysfs");
    let class = required_id(policy.get_class_id("dir"), "dir");
    let permission = required_id(policy.get_perm_id(class, "read"), "dir.read");

    let changed = policy.set_rule(
        source_type,
        target_type,
        class,
        permission,
        RuleAction::Allow,
    );
    if !changed {
        return Ok(false);
    }

    let mut output_warnings = Vec::new();
    let output = policy.to_raw(&mut output_warnings).map_err(|error| {
        format!("cannot serialize compiled policy: {error}; {output_warnings:?}")
    })?;
    for warning in output_warnings {
        eprintln!("exfatprogs-sepolicy-patcher: policy serializer warning: {warning}");
    }

    let mut verify_warnings = Vec::new();
    let mut verified = PolicyDb::from_raw(&output, &mut verify_warnings).map_err(|error| {
        format!("serialized policy failed validation: {error}; {verify_warnings:?}")
    })?;
    let verified_source = required_id(verified.get_type_id("fsck_untrusted"), "fsck_untrusted");
    let verified_target = required_id(verified.get_type_id("sysfs"), "sysfs");
    let verified_class = required_id(verified.get_class_id("dir"), "dir");
    let verified_permission = required_id(verified.get_perm_id(verified_class, "read"), "dir.read");
    if verified.set_rule(
        verified_source,
        verified_target,
        verified_class,
        verified_permission,
        RuleAction::Allow,
    ) {
        return Err("serialized policy did not retain the exact allow rule".into());
    }
    for warning in verify_warnings {
        eprintln!("exfatprogs-sepolicy-patcher: validation warning: {warning}");
    }

    // Rewrite the same inode so ExtFs retains its existing ownership, labels,
    // and other metadata associated with the policy path.
    let mut destination = OpenOptions::new()
        .write(true)
        .open(path)
        .map_err(|error| format!("cannot open policy for update: {error}"))?;
    let current_metadata = destination
        .metadata()
        .map_err(|error| format!("cannot inspect policy before update: {error}"))?;
    if current_metadata.dev() != path_metadata.dev()
        || current_metadata.ino() != path_metadata.ino()
    {
        return Err("policy file changed before update".into());
    }
    destination
        .seek(SeekFrom::Start(0))
        .and_then(|_| destination.set_len(0))
        .and_then(|_| destination.write_all(&output))
        .map_err(|error| format!("cannot write patched policy: {error}"))?;

    Ok(true)
}

fn main() {
    let mut args = env::args_os();
    let _program = args.next();
    let Some(path) = args.next() else {
        fail("usage: exfatprogs-sepolicy-patcher <compiled-policy>");
    };
    if args.next().is_some() {
        fail("expected exactly one compiled-policy path");
    }
    match patch(Path::new(&path)) {
        Ok(true) => println!("exfatprogs-sepolicy-patcher: added fsck_untrusted sysfs:dir read"),
        Ok(false) => println!("exfatprogs-sepolicy-patcher: exact allow rule already present"),
        Err(error) => fail(error),
    }
}
