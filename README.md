# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                            |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py      |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py          |        4 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py            |       59 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_base.py      |       59 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_endpoint.py  |      120 |        3 |       46 |        3 |     96% |99, 229, 232 |
| aiopnsense/client\_queue.py     |      104 |        6 |       42 |        9 |     90% |146, 173, 206-\>243, 210-\>243, 214-\>243, 218-\>243, 222-\>243, 238-\>243, 253-\>255, 261-262, 269-\>266, 271-272 |
| aiopnsense/client\_transport.py |      198 |        2 |       86 |        9 |     96% |91-\>129, 97-\>96, 115, 126-\>129, 213-\>215, 291-\>exit, 344, 355-\>358, 475-\>478 |
| aiopnsense/const.py             |       12 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py              |      288 |        5 |      126 |       10 |     96% |172-\>174, 188, 225, 230, 414, 426-\>409, 456, 520-\>522, 580-\>582, 611-\>615 |
| aiopnsense/exceptions.py        |       42 |        0 |       20 |        1 |     98% | 103-\>105 |
| aiopnsense/firewall.py          |      211 |        5 |       80 |       10 |     95% |54, 145-\>147, 147-\>149, 233, 405-\>411, 423, 429, 481, 482-\>479, 494-\>496 |
| aiopnsense/firmware.py          |       89 |        5 |       30 |        3 |     93% |64-\>66, 88, 119-120, 126-127 |
| aiopnsense/helpers.py           |      178 |        6 |       72 |        2 |     97% |107, 329, 332-335 |
| aiopnsense/nut.py               |       47 |        0 |       20 |        0 |    100% |           |
| aiopnsense/services.py          |       67 |        0 |       20 |        0 |    100% |           |
| aiopnsense/smart.py             |       37 |        0 |       12 |        0 |    100% |           |
| aiopnsense/speedtest.py         |       55 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py            |      363 |        3 |      154 |        7 |     98% |166-\>168, 168-\>170, 171, 176-\>161, 202, 205, 581-\>572 |
| aiopnsense/telemetry.py         |      247 |        3 |       70 |        3 |     98% |53, 99, 204 |
| aiopnsense/traffic.py           |      119 |        0 |       58 |        1 |     99% | 149-\>152 |
| aiopnsense/unbound.py           |      135 |        2 |       50 |        1 |     98% |   135-140 |
| aiopnsense/vnstat.py            |      214 |        9 |       94 |        9 |     94% |215, 268, 384, 411, 413, 454, 488, 508, 530 |
| aiopnsense/vouchers.py          |       51 |        1 |       16 |        3 |     94% |79, 92-\>95, 96-\>100 |
| aiopnsense/vpn.py               |      231 |        7 |      104 |       13 |     94% |167-\>exit, 237, 320-321, 477, 498, 501-\>500, 503-\>496, 533-\>532, 547-\>546, 549-\>548, 605-\>exit, 642, 648 |
| **TOTAL**                       | **2933** |   **57** | **1144** |   **84** | **97%** |           |


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