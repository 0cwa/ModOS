# SPDX-License-Identifier: AGPL-3.0-or-later
"""Boot animation is a resource, not a continuously running service."""
from ..common import manual
ID = "boot_animation"
def check(ctx):
    return [ctx.file(ID, "product-animation", "/product/media/bootanimation.zip"),
            manual(ID, "display", "Confirm actual boot-animation display at next user-observed boot")]
