# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                       |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py     |       33 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py       |       57 |        1 |       10 |        0 |     99% |       137 |
| aiopnsense/client\_base.py |      330 |       18 |      108 |       19 |     92% |82, 184-\>187, 199, 280-287, 340-\>370, 344-\>370, 348-\>370, 352-\>370, 368-\>370, 413-\>471, 421-\>420, 451, 468-\>471, 511, 528-\>531, 648-\>651, 694, 729, 764-\>766, 772-773, 780-\>777, 782-783 |
| aiopnsense/const.py        |        8 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py         |      235 |        4 |      116 |        9 |     96% |82-\>84, 99, 245, 258, 259-\>240, 288, 346-\>349, 409-\>412, 441-\>445 |
| aiopnsense/exceptions.py   |       10 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py     |      186 |       10 |       84 |       13 |     91% |77, 111, 114, 140, 143, 169, 172, 234-\>240, 252, 256, 305, 306-\>303, 318-\>320 |
| aiopnsense/firmware.py     |       87 |       10 |       28 |        4 |     88% |50-\>52, 74, 96-104, 112-113, 119-120 |
| aiopnsense/helpers.py      |      107 |        6 |       42 |        2 |     95% |43, 191, 194-197 |
| aiopnsense/services.py     |       70 |        0 |       20 |        0 |    100% |           |
| aiopnsense/speedtest.py    |       51 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py       |      330 |        3 |      146 |        7 |     98% |116-\>118, 118-\>120, 121, 126-\>111, 152, 155, 511-\>502 |
| aiopnsense/telemetry.py    |      221 |        0 |       64 |        0 |    100% |           |
| aiopnsense/unbound.py      |       48 |        0 |       18 |        0 |    100% |           |
| aiopnsense/vnstat.py       |      208 |       12 |       94 |       11 |     92% |192, 242, 306, 325-326, 336, 360, 362, 397, 429, 447, 468 |
| aiopnsense/vouchers.py     |       44 |        0 |       16 |        2 |     97% |71-\>74, 75-\>79 |
| aiopnsense/vpn.py          |      206 |        7 |      104 |       13 |     94% |130-\>exit, 200, 280-281, 420, 441, 444-\>443, 446-\>439, 475-\>474, 489-\>488, 491-\>490, 546-\>exit, 581, 587 |
| **TOTAL**                  | **2234** |   **71** |  **866** |   **80** | **95%** |           |


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