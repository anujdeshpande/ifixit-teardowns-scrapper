Teardown Data from iFixit
======

Teardowns are immensely helpful when building things. One of the many things that it helps out in is understanding the ICs that other manufacturers are using.

iFixit is one of the coolest places where there are regular teardowns. A lot of sites like engadget, etc cover stuff that comes up on iFixit.

As such, this data is not really structured. For example, if I am looking for to add an battery charger IC to my next project, I won't be able to check all the possible options that people have used in one go on iFixit (I don't think that's the point of their website anyways). But it's a great piece of information to have if say for example 15 out of 30 gadgets are using the same IC. 


### Access the data

Google spreadsheet [here](). Download it and fire up good ol Excel.

There are going to be many more such devices and I think it'll be great to structure all of this data into JSON. Could make a great CLI. But that's for another day

### How did I go about it
I first got the links to all the teardowns on iFixit using beautiful soup.  
![beautiful soup](https://cloud.githubusercontent.com/assets/969376/8167160/05b7b920-13ba-11e5-95a8-d76c21364725.jpg)  
  
Next thing was getting all the component names from all these pages. Lucky for me, there is a fairly common standard that iFixit users/staff follow when posting teardowns.  
**If it's an IC, it's in a list marked with a colored bullet**   
It's safe to assume that their css has all the possible bullet colors, even if a single teardown didn't require all the ones.

There are quite a few issues here :

1. Not all teardowns have followed the color code thing. Some just mention the IC names in simple black bullet lists. I haven't caught those cases yet.
2. Sometimes they will mention components in black bullets, even if they have colored bullets elsewhere in the same teardown.
3. Of the 609 links that I scrapped on 15th June, '15, there are quite a few repetetions (non-English versions, from different users, etc.) I haven't yet deduplicated my list (or found a clean way to do it)

### Wishlist
It'd be amazing if iFixit had some sort of syntax standard. I wouldn't have to scrap, and this kind of data would be available through their [API](https://www.ifixit.com/api/2.0/doc), which would be rad.
But that's me being a wee bit demanding of content creators.

A huge shout out to all the iFixit users and staff for their awesomeness ! Thanks for risking your expensive devices !


	Don't turn it on, take it apart
	- Dave Jones (@eevblog)
