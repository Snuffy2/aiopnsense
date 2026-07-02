# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                            |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py      |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py          |       37 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py            |       67 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_base.py      |       73 |        1 |       14 |        1 |     98% |        80 |
| aiopnsense/client\_endpoint.py  |      107 |        3 |       42 |        3 |     96% |98, 212, 215 |
| aiopnsense/client\_queue.py     |      106 |        6 |       40 |        9 |     90% |51-\>54, 67, 114, 153-\>183, 157-\>183, 161-\>183, 165-\>183, 181-\>183, 208-\>210, 216-217, 224-\>221, 226-227 |
| aiopnsense/client\_transport.py |      195 |        2 |       84 |       10 |     96% |68-\>112, 74-\>73, 92, 109-\>112, 195-\>197, 223-\>249, 273-\>exit, 313, 330-\>333, 449-\>452 |
| aiopnsense/const.py             |       11 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py              |      264 |        6 |      128 |       11 |     96% |109-\>111, 125, 162, 167, 344, 357, 358-\>339, 387, 446-\>448, 508-\>510, 538-\>542 |
| aiopnsense/exceptions.py        |       11 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py          |      175 |        3 |       62 |        6 |     96% |322-\>328, 340, 346, 398, 399-\>396, 411-\>413 |
| aiopnsense/firmware.py          |       93 |        5 |       30 |        3 |     93% |58-\>60, 82, 120-121, 127-128 |
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
| **TOTAL**                       | **2807** |   **63** | **1066** |   **84** | **96%** |           |


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