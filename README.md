# svg-cleaner

Clean up SVG files from the command line or from Python.

* optimises structure (metadata, comments, editor namespaces, empty groups)
* inlines `<style>` CSS with the real cascade and drops the block
* shortens ids and removes unused definitions
* rounds coordinates and path data to a chosen precision
* trims stroked paths against rectangular `clipPath`s (Bokeh exports)
* repairs mermaid-cli exports for Inkscape (foreignObject, labels, clusters)

Documentation is being written; `svg-cleaner --help` lists every option.
