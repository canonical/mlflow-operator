import datetime
import os

# Custom configuration for the Sphinx documentation builder.
# All configuration specific to your project should be done in this file.
#
# The file is included in the common conf.py configuration file.
# You can modify any of the settings below or add any configuration that
# is not covered by the common conf.py file.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

############################################################
### Project information
############################################################

# Product name
project = 'Charmed MLflow'
author = 'Canonical Group Ltd'

# Uncomment if your product uses release numbers
# release = '1.0'

# The default value uses the current year as the copyright year
copyright = '%s, %s' % (datetime.date.today().year, author)

## Open Graph configuration - defines what is displayed in the website preview
# The URL of the documentation output
ogp_site_url = 'https://canonical-starter-pack.readthedocs-hosted.com/'
# The documentation website name (usually the same as the product name)
ogp_site_name = 'mlflow'
# An image or logo that is used in the preview
ogp_image = 'https://assets.ubuntu.com/v1/253da317-image-document-ubuntudocs.svg'

# Update with the favicon for your product (default is the circle of friends)
html_favicon = '.sphinx/_static/favicon.png'

# (Some settings must be part of the html_context dictionary, while others
#  are on root level. Don't move the settings.)
html_context = {

    # Change to the link to your product website (without "https://")
    'product_page': 'documentation.ubuntu.com',

    # Add your product tag to ".sphinx/_static" and change the path
    # here (start with "_static"), default is the circle of friends
    'product_tag': '_static/tag.png',

    # Change to the discourse instance you want to be able to link to
    # using the :discourse: metadata at the top of a file
    # (use an empty value if you don't want to link)
    'discourse': 'https://discourse.charmhub.io/tag/mlflow',

    # Change to the GitHub info for your project
    'github_url': 'https://github.com/canonical/mlflow-operator',

    # Change to the branch for this version of the documentation
    'github_version': 'main',

    # Change to the folder that contains the documentation
    # (usually "/" or "/docs/")
    'github_folder': '/docs/',

    # Change to an empty value if your GitHub repo doesn't have issues enabled.
    # This will disable the feedback button and the issue link in the footer.
    'github_issues': 'enabled'
}

# If your project is on documentation.ubuntu.com, specify the project
# slug (for example, "lxd") here.
slug = "charmed-mlflow"

############################################################
### Redirects
############################################################

# Set up redirects (https://documatt.gitlab.io/sphinx-reredirects/usage.html)
# For example: 'explanation/old-name.html': '../how-to/prettify.html',

redirects = {}

############################################################
### Link checker exceptions
############################################################

# Links to ignore when checking links

linkcheck_ignore = [
    'http://127.0.0.1:8000',
    'https://matrix.to/#/#charmhub-mlops-kubeflow:ubuntu.com',
    'https://charmhub.io/grafana-agent-k8s/integrations#grafana-dashboards-provider',
    'https://charmhub.io/grafana-agent-k8s/integrations#send-remote-write',
    'https://charmhub.io/grafana-agent-k8s/integrations#logging-provider'
    ]

############################################################
### Additions to default configuration
############################################################

## The following settings are appended to the default configuration.
## Use them to extend the default functionality.

# Add extensions
custom_extensions = [
    'notfound.extension',
    'sphinx_sitemap'
]

custom_required_modules = [
    'sphinx-sitemap',
]

# Add files or directories that should be excluded from processing.
custom_excludes = []

# Add CSS files (located in .sphinx/_static/)
custom_html_css_files = []

# Add JavaScript files (located in .sphinx/_static/)
custom_html_js_files = []

## The following settings override the default configuration.

# Specify a reST string that is included at the end of each file.
# If commented out, use the default (which pulls the reuse/links.txt
# file into each reST file).
# custom_rst_epilog = ''

# By default, the documentation includes a feedback button at the top.
# You can disable it by setting the following configuration to True.
disable_feedback_button = False

############################################################
### Additional configuration
############################################################

## Add any configuration that is not covered by the common conf.py file.

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
