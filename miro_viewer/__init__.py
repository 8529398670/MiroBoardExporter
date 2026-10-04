"""Mobile-first, pan-and-zoom HTML viewer for boards archived by miro_exporter.

The viewer reads the exporter's snapshot folders and nothing else: it never imports
miro_exporter, so the two only share the on-disk format (manifest schema_version 2).
"""
