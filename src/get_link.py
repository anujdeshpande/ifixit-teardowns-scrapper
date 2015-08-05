# This file fetches the links for all the iFixit teardowns
from bs4 import BeautifulSoup
import urllib2, simplejson
outfile = "allLinks"
allLinks = []
for i in range(1,27): 
    response=urllib2.urlopen('https://www.ifixit.com/Teardown?page='+str(i)+'&sort=recent&filter=all')
    print i
    soup = BeautifulSoup(response.read())
    response.close()

    teardownNames = [h4.text for td in soup.find_all("div",class_="twoColGuideBlurb") for h4 in td.find_all("h4") ]
    teardownLinks = [a ['href'] for td in soup.find_all("div",class_="twoColGuideBlurb") for h4 in td.find_all("h4") for a in h4.find_all("a")]
    print len(teardownLinks)
    allLinks=allLinks+teardownLinks

print len(allLinks)
f = open ('links.txt','w')
simplejson.dump(allLinks, f)
f.close()
