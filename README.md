# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                            |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py      |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_dhcp\_leases.py    |      109 |        0 |       38 |        0 |    100% |           |
| aiopnsense/\_error\_policy.py   |       50 |        0 |       16 |        0 |    100% |           |
| aiopnsense/\_typing.py          |        6 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py            |       67 |        0 |       16 |        0 |    100% |           |
| aiopnsense/client\_base.py      |       66 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_endpoint.py  |      124 |        3 |       42 |        3 |     96% |107, 234, 237 |
| aiopnsense/client\_queue.py     |      155 |        7 |       62 |        9 |     93% |205-\>201, 207-208, 219, 246, 286, 291-\>331, 295-\>331, 299-\>331, 307-\>331, 324-\>331, 356, 366-\>362, 371 |
| aiopnsense/client\_transport.py |      202 |        1 |       68 |        3 |     99% |106-\>142, 112-\>111, 130 |
| aiopnsense/const.py             |       14 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py              |      321 |        4 |      128 |        4 |     98% |519, 556, 561, 737 |
| aiopnsense/exceptions.py        |       42 |        0 |       20 |        1 |     98% | 103-\>105 |
| aiopnsense/firewall.py          |      206 |        4 |       74 |        9 |     95% |55, 146-\>148, 148-\>150, 234, 403-\>409, 421, 476, 477-\>474, 489-\>491 |
| aiopnsense/firmware.py          |      228 |       12 |       98 |       11 |     93% |174-175, 181-182, 196-\>205, 239, 243, 304, 340, 374, 382-\>384, 407, 411-\>413, 417, 432-\>436, 437 |
| aiopnsense/helpers.py           |      197 |        5 |       82 |        2 |     97% |378, 381-384 |
| aiopnsense/nut.py               |       48 |        0 |       20 |        0 |    100% |           |
| aiopnsense/services.py          |       71 |        0 |       22 |        0 |    100% |           |
| aiopnsense/smart.py             |       38 |        0 |       12 |        0 |    100% |           |
| aiopnsense/speedtest.py         |       56 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py            |      414 |        3 |      172 |        7 |     98% |296-\>298, 298-\>300, 301, 306-\>291, 332, 335, 711-\>702 |
| aiopnsense/telemetry.py         |      248 |        3 |       70 |        3 |     98% |54, 100, 205 |
| aiopnsense/traffic.py           |      112 |        0 |       52 |        1 |     99% | 151-\>154 |
| aiopnsense/unbound.py           |      133 |        2 |       58 |        1 |     98% |   149-154 |
| aiopnsense/vnstat.py            |      215 |        9 |       94 |        9 |     94% |216, 269, 385, 412, 414, 455, 489, 509, 531 |
| aiopnsense/vouchers.py          |       51 |        1 |       16 |        3 |     94% |79, 92-\>95, 96-\>100 |
| aiopnsense/vpn.py               |      594 |       13 |      290 |       21 |     96% |226, 250, 251-\>exit, 272, 321, 425, 584, 605, 608-\>607, 610-\>603, 640-\>639, 654-\>653, 656-\>655, 712-\>exit, 745, 972, 1077, 1098, 1139, 1236, 1530-\>1526 |
| **TOTAL**                       | **3770** |   **67** | **1480** |   **87** | **97%** |           |


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