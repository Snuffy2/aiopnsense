# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                            |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py      |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py          |       29 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py            |       57 |        1 |       10 |        0 |     99% |       137 |
| aiopnsense/client\_base.py      |       73 |        1 |       14 |        1 |     98% |        80 |
| aiopnsense/client\_endpoint.py  |       60 |        1 |       20 |        1 |     98% |       112 |
| aiopnsense/client\_queue.py     |      106 |        6 |       40 |        9 |     90% |51-\>54, 66, 113, 152-\>182, 156-\>182, 160-\>182, 164-\>182, 180-\>182, 207-\>209, 215-216, 223-\>220, 225-226 |
| aiopnsense/client\_transport.py |      104 |        2 |       38 |        7 |     94% |65-\>109, 71-\>70, 89, 106-\>109, 148, 165-\>168, 284-\>287 |
| aiopnsense/const.py             |       10 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py              |      240 |        6 |      120 |       11 |     95% |88-\>90, 104, 141, 146, 249, 262, 263-\>244, 289, 346-\>348, 406-\>408, 436-\>440 |
| aiopnsense/exceptions.py        |       10 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py          |      132 |        3 |       54 |        6 |     95% |216-\>222, 234, 238, 287, 288-\>285, 300-\>302 |
| aiopnsense/firmware.py          |       87 |       10 |       28 |        4 |     88% |50-\>52, 72, 94-102, 110-111, 117-118 |
| aiopnsense/helpers.py           |      114 |        6 |       44 |        2 |     95% |46, 174, 177-180 |
| aiopnsense/services.py          |       66 |        0 |       20 |        0 |    100% |           |
| aiopnsense/speedtest.py         |       51 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py            |      326 |        3 |      146 |        7 |     98% |127-\>129, 129-\>131, 132, 137-\>122, 163, 166, 503-\>494 |
| aiopnsense/telemetry.py         |      237 |        3 |       70 |        3 |     98% |36, 76, 180 |
| aiopnsense/unbound.py           |      130 |        5 |       48 |        1 |     97% |99-106, 136-141 |
| aiopnsense/vnstat.py            |      208 |       12 |       94 |       11 |     92% |192, 242, 306, 325-326, 336, 360, 362, 397, 429, 447, 468 |
| aiopnsense/vouchers.py          |       44 |        0 |       16 |        2 |     97% |71-\>74, 75-\>79 |
| aiopnsense/vpn.py               |      214 |        7 |      104 |       13 |     94% |146-\>exit, 216, 296-297, 447, 468, 471-\>470, 473-\>466, 502-\>501, 516-\>515, 518-\>517, 573-\>exit, 608, 614 |
| **TOTAL**                       | **2301** |   **66** |  **882** |   **78** | **95%** |           |


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