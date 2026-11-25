from __future__ import annotations

import scrapy


class JobItem(scrapy.Item):
    source = scrapy.Field()
    job_id = scrapy.Field()
    title = scrapy.Field()
    company = scrapy.Field()
    location = scrapy.Field()
    salary = scrapy.Field()
    url = scrapy.Field()
    description = scrapy.Field()
    skills = scrapy.Field()
    posted_at = scrapy.Field()
    deadline = scrapy.Field()
