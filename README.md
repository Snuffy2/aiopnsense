# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                            |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py      |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py          |        4 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py            |       67 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_base.py      |       57 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_endpoint.py  |      107 |        3 |       42 |        3 |     96% |98, 212, 215 |
| aiopnsense/client\_queue.py     |       91 |        6 |       34 |        8 |     89% |63, 110, 149-\>179, 153-\>179, 157-\>179, 161-\>179, 177-\>179, 189-\>191, 197-198, 205-\>202, 207-208 |
| aiopnsense/client\_transport.py |      195 |        2 |       84 |       10 |     96% |68-\>112, 74-\>73, 92, 109-\>112, 195-\>197, 223-\>249, 273-\>exit, 313, 330-\>333, 449-\>452 |
| aiopnsense/const.py             |       12 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py              |      276 |        7 |      134 |       12 |     95% |128-\>130, 144, 181, 186, 328, 364, 377, 378-\>359, 407, 470-\>472, 532-\>534, 562-\>566 |
| aiopnsense/exceptions.py        |       11 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py          |      187 |        3 |       70 |        8 |     96% |143-\>145, 145-\>147, 377-\>383, 395, 401, 453, 454-\>451, 466-\>468 |
| aiopnsense/firmware.py          |       89 |        5 |       30 |        3 |     93% |64-\>66, 88, 119-120, 126-127 |
| aiopnsense/helpers.py           |      151 |        6 |       60 |        2 |     96% |47, 272, 275-278 |
| aiopnsense/nut.py               |       11 |        0 |        2 |        0 |    100% |           |
| aiopnsense/services.py          |       67 |        0 |       20 |        0 |    100% |           |
| aiopnsense/smart.py             |       37 |        0 |       12 |        0 |    100% |           |
| aiopnsense/speedtest.py         |       52 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py            |      357 |        3 |      154 |        7 |     98% |166-\>168, 168-\>170, 171, 176-\>161, 202, 205, 551-\>542 |
| aiopnsense/telemetry.py         |      247 |        3 |       70 |        3 |     98% |53, 99, 204 |
| aiopnsense/traffic.py           |      114 |        0 |       54 |        1 |     99% | 145-\>148 |
| aiopnsense/unbound.py           |      134 |        5 |       50 |        1 |     97% |97-104, 134-139 |
| aiopnsense/vnstat.py            |      212 |       12 |       94 |       11 |     92% |207, 260, 330, 352-353, 363, 390, 392, 433, 467, 487, 509 |
| aiopnsense/vouchers.py          |       52 |        1 |       16 |        3 |     94% |74, 87-\>90, 91-\>95 |
| aiopnsense/vpn.py               |      231 |        7 |      104 |       13 |     94% |167-\>exit, 237, 320-321, 477, 498, 501-\>500, 503-\>496, 533-\>532, 547-\>546, 549-\>548, 605-\>exit, 642, 648 |
| **TOTAL**                       | **2764** |   **63** | **1074** |   **85** | **96%** |           |


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