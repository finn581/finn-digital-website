document.addEventListener('DOMContentLoaded', () => {

    /* ---- Scroll-triggered fade-ins ---- */
    const fadeEls = document.querySelectorAll('.fade-in');
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.1 });
    fadeEls.forEach(el => observer.observe(el));

    /* ---- Card glow follows data-color attr ---- */
    document.querySelectorAll('.app-card').forEach(card => {
        const rgb = card.dataset.color;
        if (rgb) {
            card.style.setProperty('--glow-rgb', rgb);
        }
    });

    /* ---- Obfuscated contact email ---- */
    const contactLink = document.getElementById('contact-link');
    if (contactLink) {
        const user = 'contact';
        const domain = 'finndigital.net';
        contactLink.addEventListener('click', (e) => {
            e.preventDefault();
            window.location.href = 'mailto:' + user + '@' + domain;
        });
    }

    /* ---- Hero parallax on mouse move ---- */
    const hero = document.querySelector('.hero');
    const circles = document.querySelector('.hero-circles');
    const heroContent = document.querySelector('.hero-content');

    if (hero && circles && heroContent) {
        hero.addEventListener('mousemove', (e) => {
            const rect = hero.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width - 0.5;
            const y = (e.clientY - rect.top) / rect.height - 0.5;

            circles.style.transform = `translate(calc(-50% + ${x * 20}px), calc(-50% + ${y * 20}px))`;
            heroContent.style.transform = `translate(${x * -8}px, ${y * -8}px)`;
        });

        hero.addEventListener('mouseleave', () => {
            circles.style.transform = 'translate(-50%, -50%)';
            circles.style.transition = 'transform 0.6s cubic-bezier(0.16, 1, 0.3, 1)';
            heroContent.style.transform = 'translate(0, 0)';
            heroContent.style.transition = 'transform 0.6s cubic-bezier(0.16, 1, 0.3, 1)';
            setTimeout(() => {
                circles.style.transition = '';
                heroContent.style.transition = '';
            }, 600);
        });
    }
});
