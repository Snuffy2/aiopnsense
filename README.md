# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                            |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py      |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py          |       35 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py            |       57 |        1 |       10 |        0 |     99% |       137 |
| aiopnsense/client\_base.py      |       73 |        1 |       14 |        1 |     98% |        80 |
| aiopnsense/client\_endpoint.py  |      100 |        3 |       36 |        3 |     96% |98, 204, 207 |
| aiopnsense/client\_queue.py     |      106 |        6 |       40 |        9 |     90% |51-\>54, 67, 114, 153-\>183, 157-\>183, 161-\>183, 165-\>183, 181-\>183, 208-\>210, 216-217, 224-\>221, 226-227 |
| aiopnsense/client\_transport.py |      104 |        2 |       38 |        7 |     94% |65-\>109, 71-\>70, 89, 106-\>109, 148, 165-\>168, 284-\>287 |
| aiopnsense/const.py             |       10 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py              |      240 |        6 |      120 |       11 |     95% |96-\>98, 112, 150, 155, 264, 277, 278-\>259, 307, 366-\>368, 428-\>430, 458-\>462 |
| aiopnsense/exceptions.py        |       10 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py          |      132 |        3 |       54 |        6 |     95% |229-\>235, 247, 251, 303, 304-\>301, 316-\>318 |
| aiopnsense/firmware.py          |       87 |       10 |       28 |        4 |     88% |52-\>54, 77, 99-107, 115-116, 122-123 |
| aiopnsense/helpers.py           |      114 |        6 |       44 |        2 |     95% |46, 174, 177-180 |
| aiopnsense/services.py          |       66 |        0 |       20 |        0 |    100% |           |
| aiopnsense/speedtest.py         |       51 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py            |      326 |        3 |      146 |        7 |     98% |127-\>129, 129-\>131, 132, 137-\>122, 163, 166, 510-\>501 |
| aiopnsense/telemetry.py         |      237 |        3 |       70 |        3 |     98% |36, 83, 191 |
| aiopnsense/unbound.py           |      130 |        5 |       48 |        1 |     97% |99-106, 136-141 |
| aiopnsense/vnstat.py            |      208 |       12 |       94 |       11 |     92% |203, 256, 326, 348-349, 359, 386, 388, 429, 463, 483, 505 |
| aiopnsense/vouchers.py          |       48 |        1 |       16 |        3 |     94% |69, 82-\>85, 86-\>90 |
| aiopnsense/vpn.py               |      214 |        7 |      104 |       13 |     94% |149-\>exit, 219, 302-303, 459, 480, 483-\>482, 485-\>478, 515-\>514, 529-\>528, 531-\>530, 587-\>exit, 624, 630 |
| **TOTAL**                       | **2351** |   **69** |  **898** |   **81** | **95%** |           |


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