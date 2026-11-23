# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                     |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|----------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py               |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_dhcp\_leases.py             |      109 |        0 |       38 |        0 |    100% |           |
| aiopnsense/\_endpoint\_outcome.py        |       43 |        0 |        2 |        0 |    100% |           |
| aiopnsense/\_error\_policy.py            |       52 |        0 |       16 |        0 |    100% |           |
| aiopnsense/\_firmware\_request\_guard.py |       18 |        0 |        4 |        0 |    100% |           |
| aiopnsense/\_request\_failure.py         |       41 |        0 |        8 |        0 |    100% |           |
| aiopnsense/\_typing.py                   |        8 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py                     |       66 |        0 |       16 |        0 |    100% |           |
| aiopnsense/client\_base.py               |       71 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_endpoint.py           |      165 |        3 |       66 |        7 |     96% |153, 285, 288, 300-\>303, 338-\>342, 343-\>345, 348-\>350 |
| aiopnsense/client\_queue.py              |      170 |        9 |       76 |       13 |     90% |176, 251-\>247, 253-254, 292, 335-337, 341-\>393, 345-\>393, 349-\>393, 353-\>393, 357-\>393, 365-\>393, 369-\>393, 386-\>393, 418, 428-\>424, 433 |
| aiopnsense/client\_toggle.py             |      188 |        1 |       62 |        1 |     99% |       403 |
| aiopnsense/client\_transport.py          |      232 |        1 |       74 |        1 |     99% |       133 |
| aiopnsense/const.py                      |       24 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py                       |      328 |        4 |      128 |        4 |     98% |521, 559, 564, 743 |
| aiopnsense/exceptions.py                 |       45 |        0 |       22 |        1 |     99% | 109-\>111 |
| aiopnsense/firewall.py                   |      167 |        2 |       46 |        4 |     97% |56, 88-\>90, 90-\>92, 176 |
| aiopnsense/firmware.py                   |      316 |       15 |      142 |       15 |     93% |121, 237, 250, 335-336, 376, 401, 480, 484, 549, 587, 621, 629-\>631, 654, 658-\>660, 664, 679-\>683, 684 |
| aiopnsense/helpers.py                    |      204 |        5 |       86 |        2 |     98% |400, 403-406 |
| aiopnsense/nut.py                        |       48 |        0 |       20 |        0 |    100% |           |
| aiopnsense/services.py                   |       71 |        0 |       22 |        0 |    100% |           |
| aiopnsense/smart.py                      |       38 |        0 |       12 |        0 |    100% |           |
| aiopnsense/speedtest.py                  |       56 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py                     |      399 |        3 |      162 |        7 |     98% |291-\>293, 293-\>295, 296, 301-\>286, 327, 330, 706-\>697 |
| aiopnsense/telemetry.py                  |      248 |        3 |       70 |        3 |     98% |54, 100, 205 |
| aiopnsense/traffic.py                    |      112 |        0 |       52 |        1 |     99% | 151-\>154 |
| aiopnsense/unbound.py                    |       94 |        0 |       38 |        0 |    100% |           |
| aiopnsense/vnstat.py                     |      215 |        9 |       94 |        9 |     94% |216, 269, 385, 412, 414, 455, 489, 509, 531 |
| aiopnsense/vouchers.py                   |       51 |        1 |       16 |        3 |     94% |79, 92-\>95, 96-\>100 |
| aiopnsense/vpn.py                        |      490 |        7 |      224 |       15 |     97% |224, 248, 249-\>exit, 270, 319, 581, 602, 605-\>604, 607-\>600, 637-\>636, 651-\>650, 653-\>652, 709-\>exit, 956, 1250-\>1246 |
| **TOTAL**                                | **4072** |   **63** | **1526** |   **86** | **97%** |           |


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