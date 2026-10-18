# SPDX-License-Identifier: AGPL-3.0-or-later
"""microG privileged-permission crash regression plus package presence."""
import xml.etree.ElementTree as ET
from ..common import item, manual

ID = "microg"
_POLICY = "cat /product/etc/permissions/privapp-permissions-com.google.android.gms.xml"

def check(ctx):
    results = [
        ctx.package(ID, "services", "com.google.android.gms"),
        ctx.package(ID, "companion", "com.android.vending"),
    ]
    policy = ctx.probe(_POLICY)
    if policy.returncode != 0:
        results.append(item(ID, "privapp-dump", "WARNING",
                            "Could not read product GmsCore privileged-permission policy"))
    else:
        try:
            root = ET.fromstring(policy.output)
            entries = [node for node in root.iter("privapp-permissions")
                       if node.get("package") == "com.google.android.gms"]
            deny = any(n.tag == "deny-permission" and
                       n.get("name") == "android.permission.DUMP"
                       for node in entries for n in node)
            grant = any(n.tag == "permission" and
                        n.get("name") == "android.permission.DUMP"
                        for node in entries for n in node)
            status = "PASS" if deny and not grant else "FAIL"
            results.append(item(ID, "privapp-dump", status,
                                "GmsCore explicitly denied DUMP (October 17 bootloop regression)"
                                if status == "PASS" else
                                "GmsCore DUMP deny missing or conflicting grant; Android 17 bootloop risk"))
        except ET.ParseError:
            results.append(item(ID, "privapp-dump", "FAIL",
                                "Invalid product GmsCore privilege-policy XML"))
    results.append(manual(ID, "functional",
                          "Complete microG self-check, restricted signature spoofing, account, push and location tests on device"))
    return results
