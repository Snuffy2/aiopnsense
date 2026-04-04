# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                       |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py     |       29 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py       |       13 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client\_base.py |      304 |       18 |      102 |       19 |     91% |76, 177-\>180, 192, 225-232, 285-\>315, 289-\>315, 293-\>315, 297-\>315, 313-\>315, 358-\>416, 366-\>365, 396, 413-\>416, 456, 473-\>476, 593-\>596, 639, 674, 709-\>711, 717-718, 725-\>722, 727-728 |
| aiopnsense/const.py        |        6 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py         |      235 |        4 |      116 |        9 |     96% |82-\>84, 99, 242, 255, 256-\>237, 285, 340-\>343, 400-\>403, 432-\>436 |
| aiopnsense/exceptions.py   |        2 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py     |      186 |       10 |       84 |       13 |     91% |77, 111, 114, 140, 143, 169, 172, 234-\>240, 252, 256, 302, 303-\>300, 312-\>314 |
| aiopnsense/firmware.py     |       87 |       10 |       28 |        5 |     87% |33-\>41, 50-\>52, 74, 96-104, 112-113, 119-120 |
| aiopnsense/helpers.py      |      107 |        6 |       42 |        2 |     95% |43, 191, 194-197 |
| aiopnsense/services.py     |       70 |        0 |       20 |        0 |    100% |           |
| aiopnsense/speedtest.py    |       51 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py       |      329 |        3 |      146 |        7 |     98% |116-\>118, 118-\>120, 121, 126-\>111, 152, 155, 508-\>499 |
| aiopnsense/telemetry.py    |      221 |        0 |       64 |        0 |    100% |           |
| aiopnsense/unbound.py      |       48 |        0 |       18 |        0 |    100% |           |
| aiopnsense/vnstat.py       |      208 |       12 |       94 |       11 |     92% |192, 242, 306, 325-326, 336, 360, 362, 397, 429, 447, 468 |
| aiopnsense/vouchers.py     |       44 |        0 |       16 |        2 |     97% |65-\>68, 69-\>73 |
| aiopnsense/vpn.py          |      206 |        7 |      104 |       13 |     94% |124-\>exit, 194, 274-275, 414, 435, 438-\>437, 440-\>433, 469-\>468, 483-\>482, 485-\>484, 540-\>exit, 569, 575 |
| **TOTAL**                  | **2149** |   **70** |  **850** |   **81** | **95%** |           |


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