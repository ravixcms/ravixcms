"""Translatable strings of the bundled plugin/theme manifests (plugin.json / theme.json).

Manifest text is looked up with gettext at display time. This module is never imported; it only lets
``makemessages`` find the strings. Keep it in sync with the manifests (a test checks this).
"""
from django.utils.translation import gettext_noop

# plugins/contact_form/plugin.json
gettext_noop("Contact form")
gettext_noop("A public contact form at /ext/contact_form/ that stores messages in the database and has an inbox in the panel.")
gettext_noop("View and manage contact form messages")
gettext_noop("Message after sending")
gettext_noop("Your message was recorded. We will reply soon.")
gettext_noop("Notification email (optional)")
gettext_noop("If set, a notification email is sent for every new message.")
gettext_noop("Maximum messages per hour per IP")
gettext_noop("Text above the form")
gettext_noop("Contact messages")

# plugins/example_pay/plugin.json
gettext_noop("Sample gateway (gateway template)")
gettext_noop("A sample payment-gateway plugin for learning and testing. It connects to a hypothetical API (ExamplePay) and to no real service; for a real gateway change the addresses and contracts according to that provider’s official documentation.")

# plugins/seo_toolkit/plugin.json
gettext_noop("SEO toolkit")
gettext_noop("Manages the title, meta description, noindex, canonical and sharing image of each page/post, and has a global title pattern.")
gettext_noop("Manage page SEO settings")
gettext_noop("Title pattern")
gettext_noop("Use {title} and {site}.")
gettext_noop("Default robots")
gettext_noop("System default")
gettext_noop("Default description (when a page has none)")
gettext_noop("Page SEO")
gettext_noop("Edit SEO")

# themes/default/theme.json
gettext_noop("Ravix default")
gettext_noop("A readable, fast theme for blogs and content sites; no JavaScript, with a featured post and a horizontal post list.")
