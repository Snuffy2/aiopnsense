# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                       |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py     |       29 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py       |       13 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client\_base.py |      280 |       18 |       88 |       18 |     90% |69->72, 84, 117-124, 177->207, 181->207, 185->207, 189->207, 205->207, 250->308, 279, 288, 305->308, 348, 365->368, 485->488, 531, 566, 601->603, 609-610, 617->614, 619-620 |
| aiopnsense/const.py        |        6 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py         |      190 |       41 |       88 |       27 |     72% |44->46, 61, 91-102, 118, 124, 125->122, 138, 156, 163-165, 183, 186, 193, 194->181, 211, 220, 235, 248-250, 270, 271->274, 282, 300-301, 304-306, 326, 327->330, 338, 352-360 |
| aiopnsense/exceptions.py   |        2 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py     |      167 |       13 |       72 |       16 |     88% |73, 104, 107, 131, 134, 156, 159, 221->227, 239, 243, 280, 284, 285->282, 294->296, 313, 317 |
| aiopnsense/firmware.py     |       74 |       10 |       22 |        5 |     84% |27->35, 44->46, 63, 85-93, 101-102, 108-109 |
| aiopnsense/helpers.py      |       95 |        6 |       34 |        2 |     94% |43, 191, 194-197 |
| aiopnsense/services.py     |       54 |        5 |       16 |        4 |     87% |23, 31-32, 49, 51->50, 55 |
| aiopnsense/speedtest.py    |       46 |        0 |       14 |        0 |    100% |           |
| aiopnsense/system.py       |      151 |       16 |       58 |       20 |     82% |36->50, 50->68, 57, 84, 135, 142, 149, 151->155, 152->151, 156, 173, 182, 201, 215->214, 251, 252->249, 256-261, 288, 291->290 |
| aiopnsense/telemetry.py    |      177 |       23 |       42 |       11 |     82% |25-34, 46, 51, 82->84, 167, 195-196, 208->217, 218->231, 221-222, 232-236, 242, 247, 312->311, 329 |
| aiopnsense/unbound.py      |       45 |        0 |       16 |        0 |    100% |           |
| aiopnsense/vnstat.py       |      203 |       12 |       92 |       11 |     92% |183, 233, 297, 316-317, 327, 351, 353, 388, 420, 438, 459 |
| aiopnsense/vouchers.py     |       36 |        1 |       12 |        3 |     92% |35, 57->60, 61->65 |
| aiopnsense/vpn.py          |      180 |       10 |       90 |       20 |     89% |72, 77->70, 96, 97->exit, 118, 121->116, 138, 141->143, 167, 193, 370, 391, 394->393, 396->389, 425->424, 439->438, 441->440, 496->exit, 525, 531 |
| **TOTAL**                  | **1751** |  **155** |  **644** |  **137** | **87%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/Snuffy2/aiopnsense/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Snuffy2/aiopnsense/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2FSnuffy2%2Faiopnsense%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.