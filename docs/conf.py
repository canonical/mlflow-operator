import sys

sys.path.append('./')
from custom_conf import *

# Configuration file for the Sphinx documentation builder.
# You should not do any modifications to this file. Put your custom
# configuration into the custom_conf.py file.
# If you need to change this file, contribute the changes upstream.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html


#######################
# Project information #
#######################

# Project name
#
# TODO: Update with the official name of your project or product

project = "Charmed MLflow"
author = "Canonical Ltd."


# Sidebar documentation title; best kept reasonably short
#
# TODO: To include a version number, add it here (hardcoded or automated).
#
# TODO: To disable the title, set to an empty string.

html_title = project + " documentation"


# Copyright string; shown at the bottom of the page
#
# Now, the starter pack uses CC-BY-SA as the license
# and the current year as the copyright year.
#
# TODO: If your docs need another license, specify it instead of 'CC-BY-SA'.
#
# TODO: If your documentation is a part of the code repository of your project,
#       it inherits the code license instead; specify it instead of 'CC-BY-SA'.
#
# NOTE: For static works, it is common to provide the first publication year.
#       Another option is to provide both the first year of publication
#       and the current year, especially for docs that frequently change,
#       e.g. 2022–2023 (note the en-dash).
#
#       A way to check a repo's creation date is to get a classic GitHub token
#       with 'repo' permissions; see https://github.com/settings/tokens
#       Next, use 'curl' and 'jq' to extract the date from the API's output:
#
#       curl -H 'Authorization: token <TOKEN>' \
#         -H 'Accept: application/vnd.github.v3.raw' \
#         https://api.github.com/repos/canonical/<REPO> | jq '.created_at'

copyright = "%s CC-BY-SA, %s" % (datetime.date.today().year, author)


# Documentation website URL
#
# TODO: Update with the official URL of your docs or leave empty if unsure.
#
# NOTE: The Open Graph Protocol (OGP) enhances page display in a social graph
#       and is used by social media platforms; see https://ogp.me/

ogp_site_url = "https://canonical-starter-pack.readthedocs-hosted.com/"


# Preview name of the documentation website
#
# TODO: To use a different name for the project in previews, update as needed.

ogp_site_name = project


# Preview image URL
#
# TODO: To customise the preview image, update as needed.

ogp_image = "https://assets.ubuntu.com/v1/253da317-image-document-ubuntudocs.svg"


# Product favicon; shown in bookmarks, browser tabs, etc.

# TODO: To customise the favicon, uncomment and update as needed.

# html_favicon = '.sphinx/_static/favicon.png'


# Dictionary of values to pass into the Sphinx context for all pages:
# https://www.sphinx-doc.org/en/master/usage/configuration.html#confval-html_context

html_context = {
    # Product page URL; can be different from product docs URL
    #
    # TODO: Change to your product website URL,
    #       dropping the 'https://' prefix, e.g. 'ubuntu.com/lxd'.
    #
    # TODO: If there's no such website,
    #       remove the {{ product_page }} link from the page header template
    #       (usually .sphinx/_templates/header.html; also, see README.rst).
    "product_page": "documentation.ubuntu.com",
    # Product tag image; the orange part of your logo, shown in the page header
    #
    # TODO: To add a tag image, uncomment and update as needed.
    # 'product_tag': '_static/tag.png',
    # Your Discourse instance URL
    #
    # TODO: Change to your Discourse instance URL or leave empty.
    #
    # NOTE: If set, adding ':discourse: 123' to an .rst file
    #       will add a link to Discourse topic 123 at the bottom of the page.
    "discourse": "https://discourse.ubuntu.com",
    # Your Mattermost channel URL
    #
    # TODO: Change to your Mattermost channel URL or leave empty.
    "mattermost": "https://chat.canonical.com/canonical/channels/documentation",
    # Your Matrix channel URL
    #
    # TODO: Change to your Matrix channel URL or leave empty.
    "matrix": "https://matrix.to/#/#charmhub-mlops-kubeflow:ubuntu.com",
    # Your documentation GitHub repository URL
    #
    # TODO: Change to your documentation GitHub repository URL or leave empty.
    #
    # NOTE: If set, links for viewing the documentation source files
    #       and creating GitHub issues are added at the bottom of each page.
    "github_url": "https://github.com/canonical/mlflow-operator",
    # Docs branch in the repo; used in links for viewing the source files
    #
    # TODO: To customise the branch, uncomment and update as needed.
    'repo_default_branch': 'main',
    # Docs location in the repo; used in links for viewing the source files
    #


    # TODO: To customise the directory, uncomment and update as needed.
    "repo_folder": "/docs/",
    # TODO: To enable or disable the Previous / Next buttons at the bottom of pages
    # Valid options: none, prev, next, both
    # "sequential_nav": "both",
    # TODO: To enable listing contributors on individual pages, set to True
    "display_contributors": False,

    # Required for feedback button    
    'github_issues': 'enabled',
}

# TODO: To enable the edit button on pages, uncomment and change the link to a
# public repository on GitHub or Launchpad. Any of the following link domains
# are accepted:
# - https://github.com/example-org/example"
# - https://launchpad.net/example
# - https://git.launchpad.net/example
#
html_theme_options = {
    'source_edit_link': 'https://github.com/canonical/mlflow-operator',
}

# Project slug; see https://meta.discourse.org/t/what-is-category-slug/87897
#
# TODO: If your documentation is hosted on https://docs.ubuntu.com/,
#       uncomment and update as needed.

slug = 'charmed-mlflow'

#######################
# Sitemap configuration: https://sphinx-sitemap.readthedocs.io/
#######################

# Base URL of RTD hosted project

html_baseurl = 'https://documentation.ubuntu.com/charmed-mlflow/'

# URL scheme. Add language and version scheme elements.
# When configured with RTD variables, check for RTD environment so manual runs succeed:

if 'READTHEDOCS_VERSION' in os.environ:
    version = os.environ["READTHEDOCS_VERSION"]
    sitemap_url_scheme = '{version}{link}'
else:
    sitemap_url_scheme = 'MANUAL/{link}'

# Include `lastmod` dates in the sitemap:

sitemap_show_lastmod = True

#######################
# Template and asset locations
#######################

html_static_path = ["_static"]
templates_path = ["_templates"]


#############
# Redirects #
#############

# To set up redirects: https://documatt.gitlab.io/sphinx-reredirects/usage.html
# For example: 'explanation/old-name.html': '../how-to/prettify.html',

# To set up redirects in the Read the Docs project dashboard:
# https://docs.readthedocs.io/en/stable/guides/redirects.html

# NOTE: If undefined, set to None, or empty,
#       the sphinx_reredirects extension will be disabled.

redirects = {}


###########################
# Link checker exceptions #
###########################

# A regex list of URLs that are ignored by 'make linkcheck'
#
# TODO: Remove or adjust the ACME entry after you update the contributing guide
# TODO: Remove entries after https://github.com/canonical/mlflow-operator/issues/370
# is no longer an issue

linkcheck_ignore = [
    "http://127.0.0.1:8000",
    "https://github.com/canonical/ACME/*",
    "https://matrix.to/#/#charmhub-mlops-kubeflow:ubuntu.com",
    "https://snapcraft.io/yq",
    "https://snapcraft.io/rclone",
    "https://ubuntu.com/kubernetes/charmed-k8s/docs",
    "https://microk8s.io/docs/addons"
    ]


# A regex list of URLs where anchors are ignored by 'make linkcheck'

linkcheck_anchors_ignore_for_url = [
    r"https://github\.com/.*",
    r"https://charmhub\.io/grafana-agent-k8s/integrations*"
]

# give linkcheck multiple tries on failure
# linkcheck_timeout = 30
linkcheck_retries = 3

########################
# Configuration extras #
########################

# Custom MyST syntax extensions; see
# https://myst-parser.readthedocs.io/en/latest/syntax/optional.html
#
# NOTE: By default, the following MyST extensions are enabled:
#       substitution, deflist, linkify

# myst_enable_extensions = set()


# Custom Sphinx extensions; see
# https://www.sphinx-doc.org/en/master/usage/extensions/index.html

# NOTE: The canonical_sphinx extension is required for the starter pack.
#       It automatically enables the following extensions:
#       - custom-rst-roles
#       - myst_parser
#       - notfound.extension
#       - related-links
#       - sphinx_copybutton
#       - sphinx_design
#       - sphinx_reredirects
#       - sphinx_tabs.tabs
#       - sphinxcontrib.jquery
#       - sphinxext.opengraph
#       - terminal-output
#       - youtube-links

extensions = [
    'sphinx_design',
    'sphinx_tabs.tabs',
    'sphinx_reredirects',
    'youtube-links',
    'related-links',
    'custom-rst-roles',
    'terminal-output',
    'sphinx_copybutton',
    'sphinxext.opengraph',
    'myst_parser',
    'sphinxcontrib.jquery',
    'notfound.extension'
]
extensions.extend(custom_extensions)

### Configuration for extensions

# Additional MyST syntax
myst_enable_extensions = [
    'substitution',
    'deflist'
]

# Used for related links
if 'discourse' in html_context:
    html_context['discourse_prefix'] = html_context['discourse'] + '/t/'

# The default for notfound_urls_prefix usually works, but not for
# documentation on documentation.ubuntu.com
if slug:
    notfound_urls_prefix = '/' + slug + '/en/latest/'

notfound_context = {
    'title': 'Page not found',
    'body': '<h1>Page not found</h1>\n\n<p>Sorry, but the documentation page that you are looking for was not found.</p>\n<p>Documentation changes over time, and pages are moved around. We try to redirect you to the updated content where possible, but unfortunately, that didn\'t work this time (maybe because the content you were looking for does not exist in this version of the documentation).</p>\n<p>You can try to use the navigation to locate the content you\'re looking for, or search for a similar page.</p>\n',
}

# Default image for OGP (to prevent font errors, see
# https://github.com/canonical/sphinx-docs-starter-pack/pull/54 )
if not 'ogp_image' in locals():
    ogp_image = 'https://assets.ubuntu.com/v1/253da317-image-document-ubuntudocs.svg'

############################################################
### General configuration
############################################################

exclude_patterns = [
    '_build',
    'Thumbs.db',
    '.DS_Store',
    '.sphinx',
    'doc-cheat-sheet*',
]
exclude_patterns.extend(custom_excludes)

rst_epilog = '''
.. include:: /reuse/links.txt
'''
if 'custom_rst_epilog' in locals():
    rst_epilog = custom_rst_epilog

source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

if not 'conf_py_path' in html_context and 'github_folder' in html_context:
    html_context['conf_py_path'] = html_context['github_folder']

# For ignoring specific links
linkcheck_anchors_ignore_for_url = [
    r'https://github\.com/.*'
]

############################################################
### Styling
############################################################

# Find the current builder
builder = 'dirhtml'
if '-b' in sys.argv:
    builder = sys.argv[sys.argv.index('-b')+1]

# Setting templates_path for epub makes the build fail
if builder == 'dirhtml' or builder == 'html':
    templates_path = ['.sphinx/_templates']

# Theme configuration
html_theme = 'furo'
html_last_updated_fmt = ''
html_permalinks_icon = '¶'
html_theme_options = {
    'light_css_variables': {
        'font-stack': 'Ubuntu, -apple-system, Segoe UI, Roboto, Oxygen, Cantarell, Fira Sans, Droid Sans, Helvetica Neue, sans-serif',
        'font-stack--monospace': 'Ubuntu Mono, Consolas, Monaco, Courier, monospace',
        'color-foreground-primary': '#111',
        'color-foreground-secondary': 'var(--color-foreground-primary)',
        'color-foreground-muted': '#333',
        'color-background-secondary': '#FFF',
        'color-background-hover': '#f2f2f2',
        'color-brand-primary': '#111',
        'color-brand-content': '#06C',
        'color-api-background': '#cdcdcd',
        'color-inline-code-background': 'rgba(0,0,0,.03)',
        'color-sidebar-link-text': '#111',
        'color-sidebar-item-background--current': '#ebebeb',
        'color-sidebar-item-background--hover': '#f2f2f2',
        'toc-font-size': 'var(--font-size--small)',
        'color-admonition-title-background--note': 'var(--color-background-primary)',
        'color-admonition-title-background--tip': 'var(--color-background-primary)',
        'color-admonition-title-background--important': 'var(--color-background-primary)',
        'color-admonition-title-background--caution': 'var(--color-background-primary)',
        'color-admonition-title--note': '#24598F',
        'color-admonition-title--tip': '#24598F',
        'color-admonition-title--important': '#C7162B',
        'color-admonition-title--caution': '#F99B11',
        'color-highlighted-background': '#EbEbEb',
        'color-link-underline': 'var(--color-background-primary)',
        'color-link-underline--hover': 'var(--color-background-primary)',
        'color-version-popup': '#772953'
    },
    'dark_css_variables': {
        'color-foreground-secondary': 'var(--color-foreground-primary)',
        'color-foreground-muted': '#CDCDCD',
        'color-background-secondary': 'var(--color-background-primary)',
        'color-background-hover': '#666',
        'color-brand-primary': '#fff',
        'color-brand-content': '#06C',
        'color-sidebar-link-text': '#f7f7f7',
        'color-sidebar-item-background--current': '#666',
        'color-sidebar-item-background--hover': '#333',
        'color-admonition-background': 'transparent',
        'color-admonition-title-background--note': 'var(--color-background-primary)',
        'color-admonition-title-background--tip': 'var(--color-background-primary)',
        'color-admonition-title-background--important': 'var(--color-background-primary)',
        'color-admonition-title-background--caution': 'var(--color-background-primary)',
        'color-admonition-title--note': '#24598F',
        'color-admonition-title--tip': '#24598F',
        'color-admonition-title--important': '#C7162B',
        'color-admonition-title--caution': '#F99B11',
        'color-highlighted-background': '#666',
        'color-link-underline': 'var(--color-background-primary)',
        'color-link-underline--hover': 'var(--color-background-primary)',
        'color-version-popup': '#F29879'
    },
}

############################################################
### Additional files
############################################################

html_static_path = ['.sphinx/_static']

html_css_files = [
    'custom.css',
    'header.css',
    'github_issue_links.css',
]
html_css_files.extend(custom_html_css_files)

html_js_files = ['header-nav.js']
if 'github_issues' in html_context and html_context['github_issues'] and not disable_feedback_button:
    html_js_files.append('github_issue_links.js')
html_js_files.extend(custom_html_js_files)
