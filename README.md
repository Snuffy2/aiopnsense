# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                       |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py     |       29 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py       |       13 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client\_base.py |      304 |       18 |      102 |       19 |     91% |76, 177-\>180, 192, 225-232, 285-\>315, 289-\>315, 293-\>315, 297-\>315, 313-\>315, 358-\>416, 366-\>365, 396, 413-\>416, 456, 473-\>476, 593-\>596, 639, 674, 709-\>711, 717-718, 725-\>722, 727-728 |
| aiopnsense/const.py        |        6 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py         |      210 |        4 |      102 |        9 |     96% |78-\>80, 95, 223, 236, 237-\>218, 266, 317-\>320, 373-\>376, 405-\>409 |
| aiopnsense/exceptions.py   |        2 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py     |      167 |       13 |       72 |       16 |     88% |73, 105, 108, 132, 135, 157, 160, 222-\>228, 240, 244, 281, 285, 286-\>283, 295-\>297, 314, 318 |
| aiopnsense/firmware.py     |       74 |       10 |       22 |        5 |     84% |27-\>35, 44-\>46, 63, 85-93, 101-102, 108-109 |
| aiopnsense/helpers.py      |      107 |        6 |       42 |        2 |     95% |43, 191, 194-197 |
| aiopnsense/services.py     |       54 |        5 |       16 |        4 |     87% |23, 31-32, 49, 51-\>50, 55 |
| aiopnsense/speedtest.py    |       46 |        0 |       14 |        0 |    100% |           |
| aiopnsense/system.py       |      295 |        5 |      128 |       12 |     96% |38, 116-\>118, 118-\>120, 121, 126-\>111, 152, 155, 325-\>343, 453, 489-\>480, 574-\>573, 619-\>621 |
| aiopnsense/telemetry.py    |      177 |        0 |       42 |        2 |     99% |195-\>206, 221-\>231 |
| aiopnsense/unbound.py      |       44 |        0 |       16 |        0 |    100% |           |
| aiopnsense/vnstat.py       |      203 |       12 |       92 |       11 |     92% |183, 233, 297, 316-317, 327, 351, 353, 388, 420, 438, 459 |
| aiopnsense/vouchers.py     |       36 |        1 |       12 |        3 |     92% |35, 57-\>60, 61-\>65 |
| aiopnsense/vpn.py          |      181 |       10 |       90 |       20 |     89% |76, 81-\>74, 100, 101-\>exit, 122, 125-\>120, 142, 145-\>147, 171, 197, 380, 401, 404-\>403, 406-\>399, 435-\>434, 449-\>448, 451-\>450, 506-\>exit, 535, 541 |
| **TOTAL**                  | **1951** |   **84** |  **750** |  **103** | **93%** |           |


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