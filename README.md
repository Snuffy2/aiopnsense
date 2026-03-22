# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                       |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py     |       29 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py       |       13 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client\_base.py |      280 |       18 |       88 |       18 |     90% |83->86, 94, 141-148, 214->244, 218->244, 222->244, 226->244, 242->244, 294->352, 323, 332, 349->352, 401, 418->421, 571->574, 649, 684, 719->721, 727-728, 735->732, 737-738 |
| aiopnsense/const.py        |        6 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py         |      190 |       45 |       88 |       27 |     71% |31-38, 56->58, 73, 107-118, 141, 147, 148->145, 161, 179, 186-188, 212, 215, 222, 223->210, 247, 256, 271, 284-286, 312, 313->316, 324, 342-343, 346-348, 374, 375->378, 386, 400-408 |
| aiopnsense/exceptions.py   |        2 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py     |      167 |       13 |       72 |       16 |     88% |85, 117, 120, 148, 151, 177, 180, 257->263, 275, 279, 329, 333, 334->331, 343->345, 362, 366 |
| aiopnsense/firmware.py     |       74 |       10 |       22 |        5 |     84% |26->34, 47->49, 70, 92-100, 108-109, 115-116 |
| aiopnsense/helpers.py      |       95 |        6 |       34 |        2 |     94% |39, 187, 190-193 |
| aiopnsense/services.py     |       54 |        5 |       16 |        4 |     87% |26, 34-35, 57, 59->58, 63 |
| aiopnsense/speedtest.py    |       46 |        0 |       14 |        0 |    100% |           |
| aiopnsense/system.py       |      151 |       16 |       58 |       20 |     82% |46->60, 60->78, 67, 100, 163, 170, 177, 179->183, 180->179, 184, 205, 214, 240, 258->257, 300, 301->298, 305-310, 347, 350->349 |
| aiopnsense/telemetry.py    |      177 |       23 |       42 |       11 |     82% |29-38, 54, 59, 90->92, 187, 219-220, 232->241, 242->255, 245-246, 256-260, 266, 271, 348->347, 369 |
| aiopnsense/unbound.py      |       45 |        0 |       16 |        0 |    100% |           |
| aiopnsense/vnstat.py       |      203 |       12 |       92 |       11 |     92% |201, 256, 340, 367-368, 378, 409, 411, 456, 499, 522, 549 |
| aiopnsense/vouchers.py     |       36 |        1 |       12 |        3 |     92% |40, 62->65, 66->70 |
| aiopnsense/vpn.py          |      180 |       10 |       90 |       20 |     89% |85, 90->83, 113, 114->exit, 139, 142->137, 163, 166->168, 196, 226, 430, 456, 459->458, 461->454, 495->494, 509->508, 511->510, 576->exit, 613, 619 |
| **TOTAL**                  | **1751** |  **159** |  **644** |  **137** | **87%** |           |


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