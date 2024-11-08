# odwHocrBlockIiif

This is a script used for preparing newspaper files for IIIF viewers and
Lucene-based searching through ElasticSearch . Newspaper collections are typically
put together in a folder structure that reflects publishing date and both the images
and associated HOCR files are prepared beforehand. For example:
```
$ ls AECHO/1875-01-01
1875-01-01-0001.hocr      1875-01-01-0002.hocr      1875-01-01-0003.hocr      1875-01-01-0004.hocr
1875-01-01-0001.jpg       1875-01-01-0002.jpg       1875-01-01-0003.jpg       1875-01-01-0004.jpg
```
These are typically arranged in a more structured folder scheme for bigger collections, for example,
_AECHO/1871_01/1875_01_01_, but the 2 folder layout is expected for processing.

This script has quite a few options:
```
$ python odwHocrBlockIiif.py -h
usage: odwHocrBlockIiif.py [-h] [-b] [-e EXT] [-f FOLDER] [-c CONF] [-d] [-g GEOCODE] [-j] [-l LANG] [-m MIN] [-n] [-o OUT]
                           [-t TITLE] [-v]

optional arguments:
  -h, --help            show this help message and exit

named arguments:
  -b, --block           flag to create image blocks
  -e EXT, --ext EXT     extension of image format, e.g. tiff
  -f FOLDER, --folder FOLDER
                        input folder (contains hocr files)
  -c CONF, --conf CONF  set confidence number threshold for ocr words
  -d, --dir             flag to create folder of zip dirs
  -g GEOCODE, --geocode GEOCODE
                        lat,lon for newspaper
  -j, --json            flag to create JSON build file(s)
  -l LANG, --lang LANG  language for OCR
  -m MIN, --min MIN     minimum dims for para/block with word count (wxhxc), e.g. 300x200x10
  -n, --number          flag to bypass confidence value for words with number(s)
  -o OUT, --out OUT     folder for processing results
  -t TITLE, --title TITLE
                        title to set for HOCR file(s)
  -v, --vips            flag to use vips to create IIIF tiles
```
These will be fleshed out more as more experience is gained with moving
into a container deployment system. For now, processing uses these arguments:
```
python odwHocrBlockIiif.py -f AECHO -o results -b -v
```
The script has been used to create the ZIP archives used by the
[node_zipit](https://github.com/OurDigitalWorld/node_zipit) and
[browser_zipit](https://github.com/OurDigitalWorld/browser_zipit)
repositories.

The output folder has 2 types of output:
```
$ ls results
build  cloud
```
The _build_ folder has scripts for building the ElasticSearch indexes
used for discovery. The _cloud_ folder follows the structure of the input
folders:
```
$ ls results/cloud/AECHO_18750101
AECHO_18750101_images.zip  manifest.json  odw.json  odw.zip
```
The _AECHO_18750101_images.zip_ file represents the layout we use for uploading
newspaper issues to the Internet Archive. The rest of the files relate
to the images assets used for IIIF and discovery. Note that the _manifest.json_
file is a bare-bones rendering of the image information and would typically
be edited with more title or issue-specific information.
