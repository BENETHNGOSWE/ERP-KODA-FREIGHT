"""Jinja helpers for portal templates (mirrors the Laravel `return_message_status_badge`)."""

BADGES = {
    "Delivered": ("Delivered", "green"),
    "Sent": ("Sent", "blue"),
    "Queued": ("Queued", "orange"),
    "Sending": ("Sending", "orange"),
    "Failed": ("Failed", "red"),
    "Cancelled": ("Cancelled", "grey"),
    "Draft": ("Draft", "grey"),
}


def sms_status_badge(status):
    label, colour = BADGES.get(status, (status or "Unknown", "grey"))
    return f'<span class="kf-badge kf-badge-{colour}">{label}</span>'
