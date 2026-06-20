# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                            |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py      |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py          |       35 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py            |       58 |        1 |       10 |        0 |     99% |       139 |
| aiopnsense/client\_base.py      |       73 |        1 |       14 |        1 |     98% |        80 |
| aiopnsense/client\_endpoint.py  |      100 |        3 |       36 |        3 |     96% |98, 204, 207 |
| aiopnsense/client\_queue.py     |      106 |        6 |       40 |        9 |     90% |51-\>54, 67, 114, 153-\>183, 157-\>183, 161-\>183, 165-\>183, 181-\>183, 208-\>210, 216-217, 224-\>221, 226-227 |
| aiopnsense/client\_transport.py |      104 |        2 |       38 |        7 |     94% |65-\>109, 71-\>70, 89, 106-\>109, 148, 165-\>168, 284-\>287 |
| aiopnsense/const.py             |       10 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py              |      250 |        6 |      120 |       11 |     95% |108-\>110, 124, 161, 166, 275, 288, 289-\>270, 318, 377-\>379, 439-\>441, 469-\>473 |
| aiopnsense/exceptions.py        |       10 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py          |      148 |        3 |       54 |        6 |     96% |246-\>252, 264, 270, 322, 323-\>320, 335-\>337 |
| aiopnsense/firmware.py          |       89 |       10 |       28 |        4 |     88% |57-\>59, 81, 103-111, 119-120, 126-127 |
| aiopnsense/helpers.py           |      114 |        6 |       44 |        2 |     95% |46, 174, 177-180 |
| aiopnsense/services.py          |       67 |        0 |       20 |        0 |    100% |           |
| aiopnsense/smart.py             |       46 |        0 |       20 |        1 |     98% |   47-\>38 |
| aiopnsense/speedtest.py         |       52 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py            |      343 |        3 |      146 |        7 |     98% |146-\>148, 148-\>150, 151, 156-\>141, 182, 185, 525-\>516 |
| aiopnsense/telemetry.py         |      247 |        3 |       70 |        3 |     98% |53, 99, 204 |
| aiopnsense/unbound.py           |      135 |        5 |       48 |        1 |     97% |106-113, 143-148 |
| aiopnsense/vnstat.py            |      212 |       12 |       94 |       11 |     92% |207, 260, 330, 352-353, 363, 390, 392, 433, 467, 487, 509 |
| aiopnsense/vouchers.py          |       52 |        1 |       16 |        3 |     94% |74, 87-\>90, 91-\>95 |
| aiopnsense/vpn.py               |      231 |        7 |      104 |       13 |     94% |167-\>exit, 237, 320-321, 477, 498, 501-\>500, 503-\>496, 533-\>532, 547-\>546, 549-\>548, 605-\>exit, 642, 648 |
| **TOTAL**                       | **2485** |   **69** |  **918** |   **82** | **96%** |           |


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