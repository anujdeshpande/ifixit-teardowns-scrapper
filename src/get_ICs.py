# This file stores all the IC names in a file
from bs4 import BeautifulSoup
import urllib2, simplejson, pprint

f = open ('links.txt','r')
dump = simplejson.load (f)
# print dump
f.close()

divClasses = ['red',
'orange',
'yellow',
'green',
'blue',
'light_blue',
'violet']

response=urllib2.urlopen('https://www.ifixit.com/Teardown/LG+G4+Teardown/42705')
soup = BeautifulSoup(response.read())
response.close()

for i in range(0,len(divClasses)):
    bullets = [div.next.next for div in soup.find_all("div", class_="bullet_"+divClasses[i])]
    pprint.pprint (bullets)
