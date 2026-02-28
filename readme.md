Patristics
==========

https://patristics-site.vercel.app/

A site for correlating Bible verses to those who have cited them, and vice versa.

This is the pipeline that builds the above site.

It is made of three stages:
1. Scraper: grabs the data files from CCEL
2. Parser: extracts all citations from the downloaded manuscripts and puts them into a sqlite database
3. Builder: converts the database into json that can be used by the final site.

And then the Viewer itself, which is the database that can read these files.
