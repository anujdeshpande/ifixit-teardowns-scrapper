# This file stores all the IC names in a file
from bs4 import BeautifulSoup
import urllib2, simplejson, pprint
import re

f = open ('links.txt','r')
dump = simplejson.load (f)
#print dump
f.close()

divClasses = ['red',
'orange',
'yellow',
'green',
'blue',
'light_blue',
'violet',
'black']

response=urllib2.urlopen('https://www.ifixit.com/Teardown/LG+G5+Teardown/61205')
soup = BeautifulSoup(response.read())
response.close()

for i in range(0,len(divClasses)):
    print "####"
    bullets = [div.next.next for div in soup.find_all("div", class_="bullet_"+divClasses[i])]
#    pprint.pprint (bullets)
    for j in range(0,len(bullets)):
 #       print bullets[j]
        str_words=str(bullets[j])
        list_words=re.sub("[^\w]", " ",  str_words).split()
        for k in range(0,len(list_words)):
            if list_words[k].isupper() and list_words[k-1]!="the" and list_words[k-1]!="The":
                print list_words[k-1], list_words[k]

