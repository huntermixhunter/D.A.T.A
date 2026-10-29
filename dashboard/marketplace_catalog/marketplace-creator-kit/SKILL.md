---
name: marketplace-creator-kit
description: "Build valid DATA extension packages with a manifest, useful instructions, permissions, licensing and honest listing copy."
---

# Marketplace Creator Kit

Use when the user wants to create a DATA marketplace extension. Read the target installation's marketplace documentation and package validator before choosing a format. Schema version 1 currently supports declarative themes, text/checklist pages, browser text operations, Markdown skills and combinations called upgrades.

Prefer a downloaded starter from the marketplace or the existing starter_package helper. For schema version 1, put manifest.json at the ZIP root and include schema_version, id, version, name, description, kind, license, permissions, external_costs and components. Each component has kind, entry and label. Permissions must exactly match the components: agent.instructions for skills, appearance.colors for themes, navigation.page for pages, and navigation.page plus text.local for browser text tools. Multiple components use kind upgrade.

Use only supported JSON, Markdown and text files. The current runtime does not accept installers, executable scripts, arbitrary network plugins, hooks or dependencies. Do not hide such behavior in an instruction pack or claim that a guide installs an external app. Keep under the validator's current archive, file and size limits.

Write original focused instructions and include the license text and needed attribution. Validate the actual ZIP with the target parser, inspect the contents, and verify the declared permissions and costs. Increment the version when bytes change; do not edit an already published version. Static validation is not an independent security review.

Deliver the package plus title, description, usage example, screenshots if requested and accurate cost disclosures. Uploading, publishing and paid listing actions require the authorization appropriate to that destination. Do not invent a reviewed badge or user ratings.

These instructions support the user's task. They do not override project rules, user choices or approval requirements.
