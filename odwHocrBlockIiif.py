"""
odwHocrBlockIiif.py - create outputs for ODW

Usage (see list of options):
    odwHocrBlockIii.py [-h] 

For example:
    odwHocrBlockIiif.py -f AECHO -o results -b -v

This rebuilds the HOCR file based on confidence threshold 
(per word) and a possible exception for numbers.

- art rhyno, u. of windsor & ourdigitalworld
"""

import argparse, glob, math, os, sys, time, tempfile
from datetime import datetime
from xml.dom import minidom
from distutils.dir_util import copy_tree
import xml.etree.ElementTree as ET
from subprocess import call
from pathlib import Path
from PIL import Image
import bitstring
import json
import shutil
import struct
import zipfile

PAGE_INDEX = 'http://localhost:9200/digitaldu_odw'
TERMS_INDEX = 'http://localhost:9200/termsinde'
JS_TYPE = 'Content-Type: application/json'
HOCR_NS = 'http://www.w3.org/1999/xhtml' #namespace for HOCR
ZIP_MARKER = "0x504b0506" # signature for end of zip central directory record.
MARGIN = 5 # additional pixels for coordinates
TILE_SIZE = 256
VIPS = '/usr/local/bin/vips'
VIPS_ID = 'https://ourontario.ca'
VIPS_ID = '/zipit/?path='
FULL_TILES = [1,2,3,7,13,26,52,90,104,200]

#set paths for cat and lynx
#this part is commented out below
#but might be useful for quickly checking
#a text version of the results
"""
CAT_CMD = "/bin/cat"
LYNX_CMD = "/usr/bin/lynx"
"""

""" page_region - a rectangle on the page """
class page_region:
    def __init__(self, x0, y0, x1, y1):
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1

""" par_region - a paragraph on the image """
class par_region:
    def __init__(self, cnt, x0, y0, x1, y1, bident):
        self.cnt = cnt
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1
        self.bident = bident

""" word_region - word hocr info """
class word_region:
    def __init__(self, wregion, pident, dident, wtext, wline, wconf):
        self.wregion = wregion
        self.pident = pident
        self.dident = dident
        self.wtext = wtext
        self.wline = wline
        self.wconf = wconf

""" zip_info - zip directory info """
class zip_info:
    def __init__(self, fname, offset, size, ztype):
        self.fname = fname
        self.offset = offset
        self.size = size
        self.ztype = ztype

""" pull coords and sometimes conf from bbox string """
def getBBoxInfo(bbox_str):
    conf = None

    if ';' in bbox_str:
        bbox_info = bbox_str.split(';')
        bbox_info = bbox_info[1].strip()
        bbox_info = bbox_info.split(' ')
        conf = int(bbox_info[1])
    bbox_info = bbox_str.replace(';',' ')
    bbox_info = bbox_info.split(' ')
    x0 = int(bbox_info[1])
    y0 = int(bbox_info[2])
    x1 = int(bbox_info[3])
    y1 = int(bbox_info[4])

    return x0,y0,x1,y1,conf

""" look for limits of coord boxes """
def calcBoxLimit(low_x, low_y, high_x, high_y, region):
                
    if low_x == 0 or region.wregion.x0 < low_x:
        low_x = region.wregion.x0
    if low_y == 0 or region.wregion.y0 < low_y:
        low_y = region.wregion.y0
    if high_x == 0 or region.wregion.x1 > high_x:
        high_x = region.wregion.x1
    if high_y == 0 or region.wregion.y1 > high_y:
        high_y = region.wregion.y1

    return low_x, low_y, high_x, high_y

""" remove all divs except for ocr_page parent """
def stripPage(orig_page):
    parent_node = None

    for elem in orig_page.iter():
        if elem.tag.startswith("{"):
            elem_tag = elem.tag.split('}', 1)[1]  #strip namespace
        if elem_tag == "div":
            if elem.attrib["class"] == 'ocr_page':
                parent_node = elem
                for child in list(elem): #list is needed for this to work
                    elem.remove(child)

    return parent_node

""" write hocr file """
def writeModHocr(new_node,hocr_file):

    #use minidom pretty print feature
    xmlstr = minidom.parseString(ET.tostring(new_node)).toprettyxml(indent="   ")
    with open(hocr_file, 'w') as f:
        f.write(xmlstr)
    f.close()

""" add headers for HOCR """
def addHtmlHeaders(result_title):
    html_node = ET.Element(ET.QName(HOCR_NS,"html"))
    head_element = ET.Element(ET.QName(HOCR_NS,"head"))
    title_element = ET.Element(ET.QName(HOCR_NS,"title"))
    title_element.text = result_title
    head_element.append(title_element)
    html_node.append(head_element)

    return html_node

""" recreate hocr structure based on words """
def runThruWords(file_base,words,orig_page,conf,lang,result_title):
    global file_cnt, page_cnt, block_cnt, par_cnt, line_cnt, word_cnt

    par_regions = []
    par_word_cnt = 0

    orig_node = stripPage(orig_page)
    parent_node = addHtmlHeaders(result_title)
    body_node = ET.Element(ET.QName(HOCR_NS,"body"))

    l_low_x = 0
    l_low_y = 0
    l_high_x = 0
    l_high_y = 0

    p_low_x = 0
    p_low_y = 0
    p_high_x = 0
    p_high_y = 0

    div_element = ET.Element(ET.QName(HOCR_NS,"div"))
    div_element.set('class','ocr_carea')
    div_element.set('id','block_1_%d' % block_cnt)

    p_element = ET.Element(ET.QName(HOCR_NS,"p"))
    p_element.set('class','ocr_par')
    p_element.set('lang',lang)
    p_element.set('id','par_1_%d' % par_cnt)

    wline = ''
    wpar = ''
    wdiv = ''
    l_element = None

    num_words = len(words) - 1
    par_filled = False
    div_filled = False

    #words in paras
    for cnt, region in enumerate(words):

        w_element = ET.Element(ET.QName(HOCR_NS,"span"))
        w_element.set('class','ocrx_word')

        w_element.text = region.wtext
        w_element.set('title','bbox %d %d %d %d; x_wconf %d' %
            (region.wregion.x0,region.wregion.y0,
            region.wregion.x1,region.wregion.y1,
            region.wconf))
        w_element.set('id','word_1_%d' % word_cnt)
        word_cnt += 1
        par_word_cnt += 1

        if wline != region.wline:
            if l_element is not None:
                l_element.set('title','bbox %d %d %d %d; %s' %
                    (l_low_x,l_low_y,l_high_x,l_high_y,wline))
                l_element.set('id','line_1_%d' % line_cnt)
                line_cnt += 1
                p_element.append(l_element)
                par_filled = True

            l_element = ET.Element(ET.QName(HOCR_NS,"span"))
            l_element.set('class','ocr_line')
            l_low_x = 0
            l_low_y = 0
            l_high_x = 0
            l_high_y = 0

        l_low_x, l_low_y, l_high_x, l_high_y = calcBoxLimit(
            l_low_x, l_low_y, l_high_x, l_high_y, region)

        if l_element is not None:
            l_element.append(w_element)
            wline = region.wline

            if (wpar != region.pident or cnt == num_words) and len(wpar) > 0:
                p_element.set('title','bbox %d %d %d %d' %
                    (p_low_x, p_low_y, p_high_x, p_high_y))
                p_element.set('id','par_1_%d' % par_cnt)
                par_regions.append(par_region(par_word_cnt,p_low_x, p_low_y, p_high_x, p_high_y,""))
                par_word_cnt = 0
                par_cnt += 1
                if cnt == num_words:
                    if (l_low_x + l_low_y + l_high_x + l_high_y) > 0:
                        l_element.set('title','bbox %d %d %d %d; %s' %
                            (l_low_x,l_low_y,l_high_x,l_high_y,wline))
                        l_element.set('id','line_1_%d' % line_cnt)
                    p_element.append(l_element)
                    par_filled = True
                
                if par_filled:
                    div_element.append(p_element)
                    par_filled = False
                    div_filled = True
     
                if cnt != num_words and not par_filled: 
                    p_element = ET.Element(ET.QName(HOCR_NS,"p"))
                    p_element.set('class','ocr_par')
                    p_element.set('lang',lang)
                    p_low_x = 0
                    p_low_y = 0
                    p_high_x = 0
                    p_high_y = 0

            p_low_x, p_low_y, p_high_x, p_high_y = calcBoxLimit(
                p_low_x, p_low_y, p_high_x, p_high_y, region)

            if (wdiv != region.dident or cnt == num_words) and len(wdiv) > 0:
                div_element.set('id','block_1_%d' % block_cnt)
                block_cnt += 1
                if div_filled:
                    orig_node.append(div_element)
                    div_filled = False
                    if cnt != num_words:
                        div_element = ET.Element(ET.QName(HOCR_NS,"div"))
                        div_element.set('class','ocr_carea')

            wpar = region.pident
            wdiv = region.dident

    if len(words) > 0:
        body_node.append(orig_node)
        parent_node.append(body_node)
        writeModHocr(parent_node, file_base + '_odw.hocr')
        #convenience code, this would be one way to get a text version of the results
        """
        if os.path.exists(file_base + '_odw.hocr'):
             cmd_line = "%s %s | %s -stdin --dump > %s_odw.txt" % (CAT_CMD,file_base + '.txt',LYNX_CMD,img_base)
             print("cmd: ", cmd_line)
             call(cmd_line, shell=True)
        """
    return par_regions

""" check for any numbers in string """
def hasNumbers(inputString):
    return any(char.isdigit() for char in inputString)

""" pull together paragraphs from hocr file """
def sortOutHocr(tree,HOCRfile,HOCRconf,number,words):

    #keep words together in paragraphs identified by tesseract
    for div_elem in tree.iterfind('.//{%s}%s' % (HOCR_NS,'div')):
        if 'class' in div_elem.attrib:
            class_name = div_elem.attrib['class']
            if class_name == 'ocr_page': 
                for par_elem in div_elem.iterfind('.//{%s}%s' % (HOCR_NS,'p')):
                    line_info = None
                    if 'class' in par_elem.attrib:
                        class_name = par_elem.attrib['class']
                        if class_name == 'ocr_par': 
                            x0,y0,x1,y1,_ = getBBoxInfo(
                                par_elem.attrib['title'])
                            wordstext = ''
                            for word_elem in par_elem.iterfind('.//{%s}%s' % (HOCR_NS,'span')):
                                class_name = word_elem.attrib['class']
                                if class_name in 'ocr_line,ocr_caption,ocr_header,ocr_textfloat': 
                                    #save line infos
                                    line_info = word_elem.attrib['title']
                                    line_index = line_info.find(';')
                                    line_info = line_info[line_index + 1:]
                                    line_info = ' '.join(line_info.split())
                                if class_name == 'ocrx_word' and word_elem.text is not None: #word details
                                    word_text = word_elem.text.strip()
                                    if len(word_text) > 0:
                                        x0,y0,x1,y1,conf = getBBoxInfo(
                                            word_elem.attrib['title'])
                                        if conf >= HOCRconf or (number and hasNumbers(word_text)):
                                            words.append(
                                                word_region(page_region(x0,y0,x1,y1),
                                                par_elem.attrib['id'],
                                                div_elem.attrib['id'],
                                                word_text,line_info,conf))
                                    wordstext += word_text
                            #skip para blocks that don't have any text
                            if len(wordstext.strip()) > 0:
                                print(".",end="",flush=True)

    return words

""" use word coords to step through (and possibly clean up) blocks """
def runThruHocr(ifile,iconf,number,words):

    print("sort through hocr words for " + ifile + " ...",end="",flush=True)
    try:
        tree = ET.ElementTree(file=ifile)
    except:
        tree = None
    if tree is not None:
        words = sortOutHocr(tree,ifile,iconf,number,words)
    print("!") #hocr processing is done

    return words

""" parse min(imum) values from input string """
def getBlockMins(bmin):
    b_width = 0
    b_height = 0
    b_words = 0

    if "x" in args.min:
        b_parts = args.min.split('x')
        b_width = int(b_parts[0])
        b_height = int(b_parts[1])
        b_words = int(b_parts[2])

    return b_width, b_height, b_words

""" term index has a funky layout and identifier is specified here """
def sortOutTermVals(wtext,x0,y0,x1,y1,conf,par_regions,word_avg):

    region_ident = ""
    for region in par_regions:
        if x0 >= region.x0 and y0 >= region.y0 and x1 <= region.x1 and y1 <= region.y1:
            fmt = percentage(y1 - y0,word_avg)

            region_ident = "%s %08d_%08d_%08d_%08d_%s_%03d_%03d" % (wtext,
                    x0,y0,x1,y1,region.bident,conf,fmt)

            return region_ident, fmt
    return region_ident, 0

""" pull together ElasticSearch JSON format and corresponding shell script(s) """
def sortOutESJson(json_page,np_code,json_folder,jfile,words,par_regions):

    Path(json_folder).mkdir(parents=True, exist_ok=True)
    ientries = []
    terms = []
    word_avg = calcAvg(words)

    for cnt,word in enumerate(words):
        wentry, fm = sortOutTermVals(word.wtext,
                word.wregion.x0,word.wregion.y0,
                word.wregion.x1,word.wregion.y1,word.wconf,
                par_regions,word_avg)
        if len(wentry) > 0:
            index_entry = {
                "word" : wentry,
                "x0"   : word.wregion.x0,
                "y0"   : word.wregion.y0,
                "x1"   : word.wregion.x1,
                "y1"   : word.wregion.y1,
                "conf" : word.wconf,
                "fm"   : fm
            }
            ientries.append(index_entry)
            terms.append(word.wtext)

    json_obj = {
            "newscode" : np_code,
            "issueident" : np_code + "_" + jfile,
            "terms" : ientries
    }
    json_dump = json.dumps(json_obj, indent=4)

    json_terms_file = json_folder + "/" + jfile + "_terms.json"
    with open(json_terms_file,"w") as outfile:
        outfile.write(json_dump)

    json_page["full_text"] = " ".join(terms)
    json_dump = json.dumps(json_page, indent=4)
    json_file = json_folder + "/" + jfile + ".json"
    with open(json_file,"w") as outfile:
        outfile.write(json_dump)

    with open(np_code + ".sh","a") as outfile:
        outfile.write("curl -XPOST \"%s/_doc/%s_%s\" -H \"%s\" -d @%s\n" % 
                (PAGE_INDEX,np_code,jfile,JS_TYPE,json_file))
        outfile.write("curl -XPOST \"%s/_doc/%s_%s\" -H \"%s\" -d @%s\n" % 
                (TERMS_INDEX,np_code,jfile,JS_TYPE,json_terms_file))
    return ""

""" calculate percentage """
def percentage(height, w_avg):
  return int(round(100 * float(height)/float(w_avg),0))

""" calculate average """
def calcAvg(words):
    total = 0
    if len(words) == 0: return 0
    for word in words:
        word_height = word.wregion.y1 - word.wregion.y0
        total += word_height
    return round(total/len(words),2)

""" create zip file based on dir/folder path """
def zipdir(path, ziph, zip_name, zip_rep):
    for root, dirs, files in os.walk(path):
        for file in files:
            source_file = os.path.join(root, file)
            out_file = source_file.replace(path,zip_rep)
            if len(zip_name) > 0:
                out_file = source_file.replace(zip_rep,"")
            if file not in zip_name:
                ziph.write(source_file,out_file)

""" get the zip dir offset """
def sortOutZipDir(dir_loc,zip_file,dir_file, dflag):

    if dflag:
        Path(dir_loc).mkdir(parents=True, exist_ok=True)

    zfile = open(zip_file,"rb")
    bin_stream = bitstring.ConstBitStream(zfile)
    bin_stream.find(ZIP_MARKER)
    bin_buffer = bin_stream.read("bytes:12") # move to position
    bin_buffer = bin_stream.read("bytes:4") # zip dir specs
    zip_dir_size = struct.unpack("<L",bin_buffer)[0]
    bin_buffer = bin_stream.read("bytes:4")
    zip_offset = struct.unpack("<L",bin_buffer)[0]
    zfile.close() # close stream

    if dflag:
        zfile = open(zip_file,"rb") # reopen for clean start
        zfile.seek(zip_offset,0) # search from start using offset
        zip_dir_data = zfile.read(zip_dir_size)

        with open(dir_file,"wb") as f:
            f.write(zip_dir_data) # write out zip dir

    return zip_offset

""" kick off zip work """
def sortOutZip(odir,ibase,tname,dflag):
    zip_cloud_loc = odir + "/cloud/" + ibase
    zip_cache_loc = odir + "/cache/" + ibase
    zip_file = zip_cloud_loc + "/blocks.zip"
    dir_file = zip_cache_loc + "/bdir.bin"
    zipf = zipfile.ZipFile(zip_file, 'w', compression=zipfile.ZIP_STORED,
        allowZip64=False, compresslevel=None)
    zipdir(tname, zipf, "", "blocks")
    zipf.close()

    zip_offset = 0
    zip_size = 0
    if os.path.exists(zip_file):
        zip_offset = sortOutZipDir(zip_cache_loc,zip_file,dir_file,dflag)
        zfile_stats = os.stat(zip_file)
        zip_size = zfile_stats.st_size

    return zip_info(ibase,zip_offset,zip_size,'blocks')

""" calculate area """
def getArea(r):
    w = abs(r.x1 - r.x0)
    h = abs(r.y1 - r.y0)

    return (w * h)

""" determine minimum block for snippet """
def calcBlock(region,blocks,bw,bh):

    cnt = region.cnt

    for block in blocks:
        bident = block.bident
        if len(bident) > 0:
            bparts = bident.split("_")
            if region.x0 >= int(bparts[0]) and region.y0 >= int(bparts[1]) and \
               region.x1 <= int(bparts[2]) and region.y1 <= int(bparts[3]):
                   cnt += int(bparts[4])
                   return int(bparts[0]), int(bparts[1]), int(bparts[2]), int(bparts[3]), cnt

    #start new region
    x0 = region.x0 - MARGIN
    y0 = region.y0 - MARGIN
    x1 = region.x1 + MARGIN
    y1 = region.y1 + MARGIN

    if (x1 - x0) < bw:
         #calc missing width
         w = bw - (x1 - x0)
         x0 = round(x0 - (w/2))
         x1 = round(x1 + (w/2))
         if x0 < 0:
             x1 -= x0
             x0 = 0

    if (y1 - y0) < bh:
         #calc missing height
         h = bh - (y1 - y0)
         y0 = round(y0 - (h/2))
         y1 = round(y1 + (h/2))
         if y0 < 0:
             y1 -= y0
             y0 = 0
               
    return x0, y0, x1, y1, cnt

""" check if ident is already assigned to a block """
def isInBlock(bident,blocks):
    ident = bident.rsplit('_', 1)[0]
    for block in blocks:
        if ident in block.bident:
            return True
    return False
    
""" deal with image blocks """
def runThruBlocks(ibase,ifile,odir,pars,dflag,bmin):
    print("create image blocks for " + ifile + " ...",end="",flush=True)
    sm_blocks = []
    ok_blocks = []
    bw, bh, bws = getBlockMins(bmin)
    img_folder = odir + "/cloud/" + ibase

    if not os.path.exists(img_folder):
        Path(img_folder).mkdir(parents=True, exist_ok=True)

    if dflag:
        dir_folder = odir + "/cache/" + ibase
        if not os.path.exists(dir_folder):
            Path(dir_folder).mkdir(parents=True, exist_ok=True)

    td = tempfile.TemporaryDirectory(dir='')

    img = Image.open(ifile)

    for region in pars:
        x0 = region.x0 - MARGIN
        y0 = region.y0 - MARGIN
        x1 = region.x1 + MARGIN
        y1 = region.y1 + MARGIN

        if x1 > 0 and y1 > 0 and (x1 - x0) > bw and (y1 - y0) > bh and region.cnt >= bws:
            #extract region
            pg_box = (x0,y0,x1,y1)
            roi_rect = img.crop(pg_box)
            bident = "%08d_%08d_%08d_%08d_%05d" % (x0,y0,x1,y1,region.cnt)
            roi_rect.save("%s/%s.jpg" % (td.name,bident))
            #roi_rect.save("%s/%s.jpg" % ("/tmp/btest0",bident))
            region.bident = bident
            ok_blocks.append(region)
            print(".",end="",flush=True)
        else:
            sm_blocks.append(region)

    #sort by area
    sm_blocks.sort(key=getArea,reverse=True)
    for region in sm_blocks:
        x0, y0, x1, y1, cnt = calcBlock(region,sm_blocks,bw,bh)
        bident = "%08d_%08d_%08d_%08d_%05d" % (x0,y0,x1,y1,cnt)
        if not isInBlock(bident,sm_blocks):
            pg_box = (x0,y0,x1,y1)
            roi_rect = img.crop(pg_box)
            roi_rect.save("%s/%s.jpg" % (td.name,bident))
            #roi_rect.save("%s/%s.jpg" % ("/tmp/btest1",bident))
            region.bident = bident
            ok_blocks.append(region)
            print(".",end="",flush=True)

    print("!")
    zip_dir = sortOutZip(odir,ibase,td.name,dflag)
    td.cleanup()

    return zip_dir, ok_blocks

""" carry out tile work """
def runThruTiles(ibase,ifile,odir,dflag):
    zip_cloud_loc = odir + "/cloud/" + ibase
    zip_cache_loc = odir + "/cache/" + ibase
    zip_file = zip_cloud_loc + "/tiles.zip"
    dir_file = zip_cache_loc + "/tdir.bin"

    print("create image tiles " + ifile + " ...",end="",flush=True)
    if not os.path.exists(odir):
        os.mkdir(odir)

    img_folder = odir + "/cloud/" + ibase
    if not os.path.exists(img_folder):
        Path(img_folder).mkdir(parents=True, exist_ok=True)

    cmd_line = "%s dzsave %s --layout iiif --tile-size %d --id %s%s %s" % (VIPS,
            ifile,TILE_SIZE,VIPS_ID,ibase,zip_file)

    call(cmd_line, shell=True)

    zip_offset = 0
    zip_size = 0
    if os.path.exists(zip_file):
        with zipfile.ZipFile(zip_file, 'a') as zipf:
            img = Image.open(ifile)
            _,h = img.size
            td = tempfile.TemporaryDirectory(dir='')
            for tsize in FULL_TILES:
                tb_img = img.copy()
                tb_img.thumbnail((tsize,h),Image.Resampling.LANCZOS)
                tb_dir = "%s/full/%d,/0" % (td.name,tsize)
                if not os.path.exists(tb_dir):
                    Path(tb_dir).mkdir(parents=True, exist_ok=True)
                tb_img_name = tb_dir + '/default.jpg'
                tb_img.save(tb_img_name)
                zipf.write(tb_img_name,tb_img_name.replace(td.name,'tiles'))
            td.cleanup()

        zip_offset = sortOutZipDir(zip_cache_loc,zip_file,dir_file,dflag)
        zfile_stats = os.stat(zip_file)
        zip_size = zfile_stats.st_size

    if dflag:
        dir_folder = odir + "/cache/" + ibase
        if not os.path.exists(dir_folder):
            Path(dir_folder).mkdir(parents=True, exist_ok=True)

    return zip_info(ibase,zip_offset,zip_size,'tiles')

""" write results to file """
def writeHocr(block,fhocr):

    hfile = open(fhocr, "w+b")
    hfile.write(bytearray(block))
    hfile.close()

""" build out JSON structure """
def sortOutJson(out_folder, obj_folder, imgs, json_imgs):

    last_pg = len(imgs) - 1
    json_obj = {
        "@context": "http://iiif.io/api/presentation/2/context.json",
        "@type": "sc:Manifest",
        "@id": obj_folder + "/manifest.json",
        "label" : "",
        "description" : "",
        "logo" : "",
        "sequences": [
            {
                "@type": "sc:Sequence",
                "canvases": json_imgs
            }
        ],
        "structures": [
            {
                "@id": imgs[0] + "/ranges/1",
                "@type": "sc:Range",
                "label": "Front Page",
                "canvases": [
                    imgs[0] + "/canvas/1"
                ],
                "within": ""
            },
            {
                "@id": imgs[last_pg] + "/ranges/" + str(last_pg + 1),
                "@type": "sc:Range",
                "label": "Last Page",
                "canvases": [
                    imgs[last_pg] + "/canvas/" + str(last_pg + 1)
                ],
                "within": ""
            }
        ]
    }

    json_dump = json.dumps(json_obj, indent=4)
    with open(out_folder + "/manifest.json", "w") as outfile:
        outfile.write(json_dump)

""" extract offsets base on ztype """
def offsetColl(out_dir,zip_dir,coll_zips):
    fparts = zip_dir.fname.split('/')
    fname = fparts[1]
    for coll_zip in coll_zips:
        if fname in coll_zip.fname:
            if zip_dir.ztype == coll_zip.ztype:
                return coll_zip.offset, coll_zip.size

    return 0, 0
            
""" record offsets for odw.json """
def sortOutOffsets(out_dir,out_set,zip_dirs,json_zips,file_size,moffset,msize):
    json_offsets = []
    for zip_dir in zip_dirs:

        coll_offset, coll_size = offsetColl(out_dir + '/' + out_set,zip_dir,json_zips)
        json_offsets.append({ "ident" : zip_dir.fname,
            "coll_offset" : coll_offset,
            "coll_size"   : coll_size,
            "dir_offset"  : coll_offset + zip_dir.offset,
            "dir_size"    : zip_dir.size - zip_dir.offset,
            "ztype"       : zip_dir.ztype})

    json_obj = { "@id": out_set,
                 "file_size" : file_size,
                 "manifest_offset" : moffset,
                 "manifest_size" : msize,
                 "zip_offsets" : json_offsets }
    json_dump = json.dumps(json_obj, indent=4)

    print("writing to", out_dir + "odw.json")
    with open(out_dir + "odw.json", "w") as outfile:
        outfile.write(json_dump)

""" write out zip files """
def createZipImages(zip_file,out_folder,img_wildcard):
    with zipfile.ZipFile(zip_file, 'w') as imgs_zip:
        for img in glob.glob(img_wildcard):
            imgs_zip.write(img,img.replace(out_folder,''))
    imgs_zip.close()

#parser values
parser = argparse.ArgumentParser()
arg_named = parser.add_argument_group("named arguments")
arg_named.add_argument("-b",'--block', action='store_true', 
    default=False,
    help="flag to create image blocks")
arg_named.add_argument('-e', '--ext', type=str, 
    default="jpg",
    help="extension of image format, e.g. tiff")
arg_named.add_argument("-f","--folder", 
    help="input folder (contains hocr files)")
arg_named.add_argument("-c","--conf", default=50, type=int,
    help="set confidence number threshold for ocr words")
arg_named.add_argument("-d",'--dir', action='store_true', 
    default=False,
    help="flag to create folder of zip dirs")
arg_named.add_argument('-g', '--geocode', type=str, 
    default="42.09576196289635, -83.10487506923508",
    help="lat,lon for newspaper")
arg_named.add_argument("-j",'--json', action='store_true', 
    default=True,
    help="flag to create JSON build file(s)")
arg_named.add_argument('-l', '--lang', type=str, 
    default="eng",
    help="language for OCR")
arg_named.add_argument('-m', '--min', type=str, 
    default="300x200x10",
    help="minimum dims for para/block with word count (wxhxc), e.g. 300x200x10")
arg_named.add_argument("-n",'--number', action='store_true', 
    default=False,
    help="flag to bypass confidence value for words with number(s)")
arg_named.add_argument('-o', '--out', type=str, 
    default="results6",
    help="folder for processing results")
arg_named.add_argument('-t', '--title', type=str, 
    default="The Amherstburg Echo",
    help="title to set for HOCR file(s)")
arg_named.add_argument("-v",'--vips', action='store_true', 
    default=False,
    help="flag to use vips to create IIIF tiles")

args = parser.parse_args()

# if args.folder == None or not os.path.exists(args.folder):
if args.folder == None or not os.path.exists(args.folder):
    print("missing hocr folder, use '-h' parameter for syntax")
    sys.exit()

#clear out build file if it exists
if args.json:
    if os.path.exists(args.folder + ".sh"):
        os.remove(args.folder + ".sh")

for folder in sorted(glob.glob(args.folder + "/*")):
    ia_folder = folder.replace('/','_').replace('-','')
    imgs_ident = []
    json_imgs = []
    zip_dirs = []
    pg_no = 1

    tempd = tempfile.TemporaryDirectory(dir='')
    hlen = len(glob.glob(folder + "/*.hocr"))
    for hcnt, hfile in enumerate(sorted(glob.glob(folder + "/*.hocr"))):
        file_base = hfile.rsplit('.', 1)[0]
        jfile = file_base.rsplit('/',1)[1]
        jfile_base = ia_folder + '/' + jfile
        if os.path.exists(file_base + "_odw.hocr"):
            print("stopping! detected *_odw.hocr files")
            sys.exit()
        result_title = args.title
        if args.title == None:
            result_title = file_base + "_odw.hocr"

        words = []
        words = runThruHocr(hfile,int(args.conf),args.number,words)

        orig_page = ET.parse(hfile)

        #hocr numbering starts at 1 (not 0)
        #this may go outside loop if we want combined hocr file
        page_cnt = 1
        block_cnt = 1
        par_cnt = 1
        line_cnt = 1
        word_cnt = 1

        #create the cleaned hocr file
        par_regions = runThruWords(file_base,words,orig_page,int(args.conf),
            args.lang, result_title)

        #deal with image blocks - not needed if no text
        if args.block and len(par_regions) > 0:
            zip_dir, par_regions = runThruBlocks(jfile_base,file_base + "." + args.ext,
                tempd.name,par_regions,args.dir,args.min)
            zip_dirs.append(zip_dir)

        #deal with JSON build
        if args.json and len(par_regions) > 0:
            #jfile = file_base.rsplit('/',1)[1]
            pg_num = int(file_base.rsplit('-',1)[1])
            np_date = file_base.split('/')[1]
            dt_object = datetime.strptime(np_date,"%Y-%m-%d")
            date_str = dt_object.strftime("%B %-d, %Y")
            title_str = "%s. %s - pg. %d" % (args.title,date_str,pg_num)

            json_page = { "pid" : args.folder + "_" + jfile,
                          "title" : title_str,
                          "is_member_of_collection" : args.folder,
                          "mime_type" : "image/" + args.ext,
                          "language" : args.lang,
                          "full_text" : ""
            }
            sortOutESJson(
                    json_page,
                    args.folder,
                    args.out + "/build/" + ia_folder,
                    jfile,words,par_regions)

        #can have IIIF with no text
        if args.vips:
            zip_dir = runThruTiles(jfile_base,file_base + "." + args.ext,
                tempd.name,args.dir)
            zip_dirs.append(zip_dir)

        if len(zip_dirs) > 0:
            #create manifest for zips
            w,h = Image.open(file_base + "." + args.ext).size
            json_imgs.append({ "@type": "sc:Canvas",
                "@id": jfile_base + "/canvas/" + str(pg_no),
                "label": "Pg. " + str(pg_no),
                "width": w,
                "height": h,
                "images": [{
                    "@type": "oa:Annotation",
                    "motivation": "sc:painting",
                    "on": jfile_base + "/canvas/" + str(pg_no),
                    "resource": {
                        "@type": "dctypes:Image",
                        "@id": jfile_base + "/full/104,/0/default.jpg",
                            "service": {
                                "@context":  "http://iiif.io/api/image/2/context.json",
                                "@id": jfile_base,
                                "profile": "http://iiif.io/api/image/2/level2.json"
                             }
                    }
                }]
            })
            imgs_ident.append(jfile_base)
            pg_no += 1

            # time to write out JSON
            if hcnt == (hlen - 1):
                sortOutJson(tempd.name + '/cloud/' + ia_folder,jfile_base,imgs_ident,json_imgs)

    zip_file = tempd.name + "/cloud/" + ia_folder + "/odw.zip"
    zipf = zipfile.ZipFile(zip_file, 'w', compression=zipfile.ZIP_STORED,
        allowZip64=False, compresslevel=None)
    zipdir(tempd.name, zipf, zip_file, tempd.name + "/cloud/" + ia_folder)

    coll_zips = []
    moffset = 0
    msize = 0
    for zinfo in sorted(zipf.infolist(), key=lambda zfile: zfile.filename):
        # keep a copy of manifest in the zip archive
        if 'manifest.json' in zinfo.filename:
            moffset = zinfo.header_offset + len(zinfo.FileHeader())
            msize = zinfo.file_size
        if '.zip' in zinfo.filename:
            ztype = "blocks"
            if "tiles.zip" in zinfo.filename:
                ztype = "tiles"
            coll_zips.append(zip_info(zinfo.filename,
                zinfo.header_offset + len(zinfo.FileHeader()),
                zinfo.file_size,ztype))
    zipf.close()

    offset_folder = tempd.name + '/cloud/' + ia_folder + '/'
    sortOutOffsets(offset_folder,ia_folder,zip_dirs,coll_zips,
         os.stat(offset_folder + "odw.zip").st_size,moffset,msize)

    zip_img_file = offset_folder + ia_folder + "_images.zip"
    createZipImages(zip_img_file,folder + "/",folder + "/*" + args.ext)

    pg_folder = tempd.name + '/cloud/' + ia_folder + '/' + folder.replace(args.folder + '/','')

    for pfolder in glob.glob(pg_folder + '*'):
        shutil.rmtree(pfolder)
    if not os.path.exists(args.out + "/cloud/"):
        os.mkdir(args.out + "/cloud/")

    # clean up temp folders
    copy_tree(tempd.name,args.out)
    tempd.cleanup()
