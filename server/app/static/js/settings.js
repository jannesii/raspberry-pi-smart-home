document.addEventListener('DOMContentLoaded', function() {
  console.log('Showing flash for 3 seconds');
    setTimeout(pop_flashes, 3000);
    // Vahvistuspoisto
    const delForm = document.getElementById('deleteForm');
    if (delForm) {
      delForm.addEventListener('submit', function(e) {
        if (!confirm('Haluatko varmasti poistaa valitun käyttäjän?')) {
          e.preventDefault();
        }
      });
    }
  });

async function pop_flashes() {
    console.log('Removing flash messages');
    document.querySelectorAll('.flash').forEach(el => el.remove());
  }
