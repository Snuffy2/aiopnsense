# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                            |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py      |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py          |        6 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py            |       59 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_base.py      |       62 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_endpoint.py  |      125 |        3 |       46 |        3 |     96% |106, 233, 236 |
| aiopnsense/client\_queue.py     |      108 |        6 |       46 |        7 |     92% |147, 174, 207-\>247, 211-\>247, 215-\>247, 223-\>247, 240-\>247, 265-266, 273-\>270, 275-276 |
| aiopnsense/client\_transport.py |      205 |        1 |       84 |        5 |     98% |93-\>131, 99-\>98, 117, 128-\>131, 292-\>exit |
| aiopnsense/const.py             |       14 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py              |      364 |        5 |      164 |       10 |     97% |259-\>261, 280, 317, 322, 540, 552-\>535, 582, 646-\>648, 706-\>708, 737-\>741 |
| aiopnsense/exceptions.py        |       42 |        0 |       20 |        1 |     98% | 103-\>105 |
| aiopnsense/firewall.py          |      205 |        4 |       74 |        9 |     95% |54, 145-\>147, 147-\>149, 233, 402-\>408, 420, 475, 476-\>473, 488-\>490 |
| aiopnsense/firmware.py          |      221 |       12 |       98 |       11 |     93% |139-140, 146-147, 161-\>170, 204, 208, 269, 305, 339, 347-\>349, 372, 376-\>378, 382, 397-\>401, 402 |
| aiopnsense/helpers.py           |      219 |        5 |       92 |        2 |     98% |443, 446-449 |
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
| aiopnsense/vpn.py               |      480 |       11 |      232 |       19 |     96% |223, 247, 248-\>exit, 269, 318, 422, 581, 602, 605-\>604, 607-\>600, 637-\>636, 651-\>650, 653-\>652, 709-\>exit, 751, 769, 798, 895, 1191-\>1187 |
| **TOTAL**                       | **3502** |   **65** | **1416** |   **91** | **97%** |           |


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