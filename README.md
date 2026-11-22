# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                            |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py      |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_dhcp\_leases.py    |      109 |        0 |       38 |        0 |    100% |           |
| aiopnsense/\_typing.py          |        6 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py            |       67 |        0 |       16 |        0 |    100% |           |
| aiopnsense/client\_base.py      |       66 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_endpoint.py  |      125 |        3 |       46 |        3 |     96% |106, 233, 236 |
| aiopnsense/client\_queue.py     |      151 |        7 |       60 |        9 |     92% |201-\>197, 203-204, 215, 242, 282, 287-\>327, 291-\>327, 295-\>327, 303-\>327, 320-\>327, 352, 362-\>358, 367 |
| aiopnsense/client\_transport.py |      209 |        1 |       84 |        5 |     98% |109-\>147, 115-\>114, 133, 144-\>147, 308-\>exit |
| aiopnsense/const.py             |       14 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py              |      320 |        4 |      128 |        4 |     98% |526, 563, 568, 744 |
| aiopnsense/exceptions.py        |       42 |        0 |       20 |        1 |     98% | 103-\>105 |
| aiopnsense/firewall.py          |      205 |        4 |       74 |        9 |     95% |54, 145-\>147, 147-\>149, 233, 402-\>408, 420, 475, 476-\>473, 488-\>490 |
| aiopnsense/firmware.py          |      227 |       12 |       98 |       11 |     93% |174-175, 181-182, 196-\>205, 239, 243, 304, 340, 374, 382-\>384, 407, 411-\>413, 417, 432-\>436, 437 |
| aiopnsense/helpers.py           |      222 |        5 |       94 |        2 |     98% |453, 456-459 |
| aiopnsense/nut.py               |       47 |        0 |       20 |        0 |    100% |           |
| aiopnsense/services.py          |       70 |        0 |       22 |        0 |    100% |           |
| aiopnsense/smart.py             |       37 |        0 |       12 |        0 |    100% |           |
| aiopnsense/speedtest.py         |       55 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py            |      414 |        3 |      174 |        7 |     98% |300-\>302, 302-\>304, 305, 310-\>295, 336, 339, 715-\>706 |
| aiopnsense/telemetry.py         |      247 |        3 |       70 |        3 |     98% |53, 99, 204 |
| aiopnsense/traffic.py           |      119 |        0 |       58 |        1 |     99% | 149-\>152 |
| aiopnsense/unbound.py           |      135 |        2 |       50 |        1 |     98% |   135-140 |
| aiopnsense/vnstat.py            |      214 |        9 |       94 |        9 |     94% |215, 268, 384, 411, 413, 454, 488, 508, 530 |
| aiopnsense/vouchers.py          |       51 |        1 |       16 |        3 |     94% |79, 92-\>95, 96-\>100 |
| aiopnsense/vpn.py               |      601 |       13 |      292 |       21 |     96% |228, 252, 253-\>exit, 274, 323, 427, 586, 607, 610-\>609, 612-\>605, 642-\>641, 656-\>655, 658-\>657, 714-\>exit, 747, 974, 1079, 1100, 1141, 1238, 1534-\>1530 |
| **TOTAL**                       | **3756** |   **67** | **1496** |   **89** | **97%** |           |


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